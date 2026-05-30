"""FastAPI app: orchestrates the section-by-section landing-page build.

Exposes both:
  - Legacy section-by-section UI API  (/api/session/*)
  - New async clone API               (POST /api/clone, GET /api/clone/{job_id})

Sessions and clone jobs live in memory and reset on restart.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import re
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

# ── CSS namespacing helpers (Fix C2) ─────────────────────────────────────────

# At-rules whose entire block must pass through untouched (no selector prefixing)
_CSS_PASSTHROUGH_AT = re.compile(
    r"@(?:keyframes|font-face|page|import|charset|namespace|counter-style|font-feature-values)\b",
    re.IGNORECASE,
)
# At-rules that wrap real selector blocks (recurse inside them)
_CSS_CONDITIONAL_AT = re.compile(
    r"@(?:media|supports|document|layer)\b",
    re.IGNORECASE,
)
# Tags/pseudo-selectors that should map to the scope root itself
_CSS_ROOT_SELECTORS = re.compile(
    r"^(html|body|:root|\*)$",
    re.IGNORECASE,
)


def _prefix_selector_list(selector_list: str, scope: str) -> str:
    """Prefix every comma-separated selector with *scope*.

    Selectors that match html/body/:root/* become *scope* itself so that
    resets stay scoped to the section wrapper rather than leaking globally.
    """
    out = []
    for sel in selector_list.split(","):
        s = sel.strip()
        if not s:
            continue
        if _CSS_ROOT_SELECTORS.match(s):
            out.append(scope)
        else:
            out.append(f"{scope} {s}")
    return ", ".join(out)


def namespace_css(css: str, scope: str) -> str:
    """Rewrite all selectors in *css* so they are descendants of *scope*.

    Uses a brace-depth scanner — no external CSS-parser dependency.
    Handles:
    - Regular rules: `selector { … }`
    - Conditional at-rules: `@media(…) { selector { … } }`
    - Passthrough at-rules: `@keyframes`, `@font-face`, `@import`, etc. (untouched)
    - Comma-separated selector lists
    - Nested braces inside conditional blocks
    """
    result = []
    i = 0
    n = len(css)

    def scan_block(start: int, scope: str, depth: int = 0) -> int:
        """Scan from *start*, emit into *result*, return index after closing }."""
        j = start
        prelude_start = j  # start of current prelude (selector / at-rule text)

        while j < n:
            ch = css[j]

            if ch == "{":
                prelude = css[prelude_start:j].strip()

                if depth == 0:
                    # Top-level rule
                    if _CSS_PASSTHROUGH_AT.match(prelude):
                        # e.g. @keyframes — copy verbatim to closing brace (may be nested)
                        brace_depth = 1
                        result.append(f"{prelude} {{")
                        j += 1
                        while j < n and brace_depth > 0:
                            if css[j] == "{":
                                brace_depth += 1
                            elif css[j] == "}":
                                brace_depth -= 1
                                if brace_depth == 0:
                                    result.append("}")
                                    j += 1
                                    break
                            result.append(css[j])
                            j += 1
                    elif _CSS_CONDITIONAL_AT.match(prelude):
                        # e.g. @media — emit the condition, then recurse inside
                        result.append(f"{prelude} {{")
                        j = scan_block(j + 1, scope, depth=1)
                        result.append("}")
                    else:
                        # Regular selector rule
                        if prelude:
                            result.append(f"{_prefix_selector_list(prelude, scope)} {{")
                        else:
                            result.append("{")
                        # Copy rule body verbatim until its closing brace
                        brace_depth = 1
                        j += 1
                        while j < n and brace_depth > 0:
                            if css[j] == "{":
                                brace_depth += 1
                            elif css[j] == "}":
                                brace_depth -= 1
                                if brace_depth == 0:
                                    result.append("}")
                                    j += 1
                                    break
                            result.append(css[j])
                            j += 1

                else:
                    # Depth ≥ 1 (inside a conditional at-rule) — prefix selector
                    if prelude:
                        result.append(f"{_prefix_selector_list(prelude, scope)} {{")
                    else:
                        result.append("{")
                    brace_depth = 1
                    j += 1
                    while j < n and brace_depth > 0:
                        if css[j] == "{":
                            brace_depth += 1
                        elif css[j] == "}":
                            brace_depth -= 1
                            if brace_depth == 0:
                                result.append("}")
                                j += 1
                                break
                        result.append(css[j])
                        j += 1

                prelude_start = j

            elif ch == "}" and depth > 0:
                # Closing brace of the containing conditional at-rule
                return j + 1

            else:
                j += 1

        return j

    scan_block(0, scope)
    return "".join(result)


# HTML style-block pattern (lazy, handles multiline)
_STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL | re.IGNORECASE)
# Tags that must not appear inside a section fragment
_DOC_WRAPPER_RE = re.compile(
    r"<!DOCTYPE[^>]*>|<html[^>]*>|</html>|<head[^>]*>.*?</head>|<body[^>]*>|</body>",
    re.DOTALL | re.IGNORECASE,
)


def namespace_fragment(fragment: str, section_id: str) -> str:
    """Wrap *fragment* in a scoped div and namespace all its <style> blocks.

    Steps:
    1. Strip any document-wrapper tags (<!DOCTYPE>, <html>, <head>…</head>, <body>).
    2. Extract every <style>…</style> block and namespace its CSS.
    3. Wrap remaining markup in <div id="{section_id}">.
    4. Re-insert the scoped <style> blocks inside the wrapper (before content).
    """
    scope = f"#{section_id}"

    # 1. Strip document wrappers (only present when sentinel was missing)
    clean = _DOC_WRAPPER_RE.sub("", fragment).strip()

    # 2. Extract and namespace <style> blocks
    scoped_styles: list[str] = []

    def _namespace_style(m: re.Match) -> str:
        raw_css = m.group(1)
        scoped = namespace_css(raw_css, scope)
        scoped_styles.append(f"<style>{scoped}</style>")
        return ""  # remove from markup

    markup = _STYLE_BLOCK_RE.sub(_namespace_style, clean).strip()

    # 3+4. Assemble: wrapper → scoped styles → content
    style_block = "\n".join(scoped_styles)
    return f'<div id="{section_id}">\n{style_block}\n{markup}\n</div>'

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

load_dotenv()

from backend.gemini_openai import GeminiSession, build_client  # noqa: E402
from backend.s3 import upload_html, upload_image  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("landing-builder")

app = FastAPI(title="Vision-to-Code Landing Builder", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_gemini_client = build_client()
_executor = ThreadPoolExecutor(max_workers=8)              # Gemini / OpenAI / S3 blocking calls
_clone_executor = ThreadPoolExecutor(max_workers=2)        # outer clone-job threads (own event loop)

ROOT = Path(__file__).parent.parent
FRONTEND_INDEX = ROOT / "frontend" / "index.html"


# ---------------------------------------------------------------------------
# Shared orchestration helpers (used by both FastAPI endpoint and CLI)
# ---------------------------------------------------------------------------

def create_session_state(
    s3_bucket: str | None = None,
    s3_folder: str | None = None,
) -> dict:
    """Build a fresh session dict for one landing-page job.

    Args:
        s3_bucket: Target S3 bucket for images + final HTML.
                   Defaults to S3_BUCKET env var when None (FastAPI / legacy path).
        s3_folder: Key prefix for this job (e.g. "pasta").
                   Images land at {s3_folder}/images/<uuid>.png.
                   Defaults to "landing-builder" when None.
    """
    return {
        "gemini": GeminiSession(_gemini_client),
        "html": "",
        "image_count": 0,
        "section_count": 0,
        "image_urls": {},       # marker_index (int) -> S3 URL
        "scaffold_open": "",    # everything before <!-- SECTION_START --> in section 1
        "scaffold_close": "",   # everything after <!-- SECTION_END --> in section 1
        "sections_html": "",    # accumulated fragment content between sentinels
        "s3_bucket": s3_bucket,         # None → falls back to S3_BUCKET env var
        "s3_folder": s3_folder,         # None → uses default "landing-builder" prefix
    }


async def process_section(session: dict, image_bytes: bytes,
                           brand_prompt: str, executor) -> dict:
    """Run one screenshot through Gemini → image gen → S3 → sentinel parse → fragment append.

    Mutates *session* in place.
    Returns {section_count, image_count, new_images, html_bytes, unfilled_placeholders}.
    Raises RuntimeError on Gemini / image-pipeline failures.
    """
    loop = asyncio.get_running_loop()

    # ── Gemini text call ──────────────────────────────────────────────────
    try:
        section = await loop.run_in_executor(
            executor,
            functools.partial(
                session["gemini"].add_section,
                image_bytes,
                brand_prompt,
                session["image_count"],   # next_marker_index
            ),
        )
    except Exception as e:
        log.exception("Gemini text call failed")
        raise RuntimeError(f"Gemini text generation failed: {e}") from e

    new_images = section.image_prompts
    base_index = session["image_count"]
    is_first_section = session["section_count"] == 0

    # ── Sentinel parsing ─────────────────────────────────────────────────────
    START_SENTINEL = "<!-- SECTION_START -->"
    END_SENTINEL   = "<!-- SECTION_END -->"
    raw_html = section.html

    # Minimal scaffold used when section 1 omits the sentinels (Fix C1)
    _MINIMAL_SCAFFOLD_OPEN  = (
        '<!DOCTYPE html><html lang="es"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Landing</title>'
        '</head><body>'
    )
    _MINIMAL_SCAFFOLD_CLOSE = "</body></html>"

    if START_SENTINEL in raw_html and END_SENTINEL in raw_html:
        start_idx = raw_html.index(START_SENTINEL)
        end_idx   = raw_html.index(END_SENTINEL)
        fragment  = raw_html[start_idx + len(START_SENTINEL):end_idx]
        if is_first_section:
            session["scaffold_open"]  = raw_html[:start_idx]
            session["scaffold_close"] = raw_html[end_idx + len(END_SENTINEL):]
            log.info("Scaffold captured: open=%d chars, close=%d chars",
                     len(session["scaffold_open"]), len(session["scaffold_close"]))
    else:
        log.warning("Sentinel comments missing for section %d", session["section_count"] + 1)
        if is_first_section:
            # FIX C1: synthesise a minimal valid scaffold so subsequent sections
            # are never appended after </html>. Strip any doc-wrapper tags from
            # the fragment (namespace_fragment will clean them too, but be explicit).
            session["scaffold_open"]  = _MINIMAL_SCAFFOLD_OPEN
            session["scaffold_close"] = _MINIMAL_SCAFFOLD_CLOSE
            log.warning("Section 1 sentinel missing — synthesised minimal scaffold")
        # Use the full raw response as the fragment (doc wrappers stripped by namespace_fragment)
        fragment = raw_html

    # ── Diagnostic logging ────────────────────────────────────────────────
    log.info("=== DIAGNOSTIC: Section #%d ===", session["section_count"] + 1)
    for offset, spec in enumerate(new_images):
        prompt_preview = spec["prompt"][:150].replace("\n", " ")
        log.info("  Image[%d] (marker __IMG_%d__): aspect_ratio=%s | prompt=%s...",
                 offset, base_index + offset, spec.get("aspect_ratio", "unknown"), prompt_preview)
    img_count = fragment.count("<img")
    markers_in_fragment = re.findall(r"__IMG_\d+__", fragment)
    unique_markers = list(dict.fromkeys(markers_in_fragment))
    log.info("  Fragment: %d <img> tags, %d unique markers: %s",
             img_count, len(unique_markers), unique_markers)
    if len(unique_markers) != len(new_images):
        log.warning("  ⚠ SPEC/MARKER MISMATCH: %d specs vs %d unique markers",
                    len(new_images), len(unique_markers))
    log.info("=== END DIAGNOSTIC ===")

    log.info("Section returned %d new image specs (base index=%d)", len(new_images), base_index)

    # ── Image generation + S3 upload (parallel) ───────────────────────────
    if new_images:
        # Determine upload destination for this job's images
        img_bucket = session.get("s3_bucket")   # None → upload_image uses S3_BUCKET env var
        img_prefix = (
            f"{session['s3_folder']}/images"
            if session.get("s3_folder")
            else "landing-builder"
        )

        async def _gen_and_upload(offset: int, spec: dict) -> tuple[int, str]:
            img_bytes = await loop.run_in_executor(
                executor,
                session["gemini"].generate_image,
                spec["prompt"],
                spec.get("aspect_ratio", "1:1"),
            )
            url = await loop.run_in_executor(
                executor,
                functools.partial(
                    upload_image,
                    img_bytes,
                    "png",
                    img_bucket,
                    img_prefix,
                ),
            )
            return base_index + offset, url

        try:
            results = await asyncio.gather(
                *[_gen_and_upload(i, spec) for i, spec in enumerate(new_images)]
            )
        except Exception as e:
            log.exception("Image generation/upload failed")
            raise RuntimeError(f"Image pipeline failed: {e}") from e

        for marker_index, url in results:
            session["image_urls"][marker_index] = url

        # Substitute only this section's markers in the fragment
        for marker_index in range(base_index, base_index + len(new_images)):
            if marker_index in session["image_urls"]:
                placeholder = f"__IMG_{marker_index}__"
                fragment = fragment.replace(placeholder, session["image_urls"][marker_index])

    leftover = re.findall(r"__IMG_\d+__", fragment)
    if leftover:
        log.warning("Unfilled placeholders remain in fragment: %s", leftover)

    # ── FIX C2: deterministic CSS namespacing ─────────────────────────────
    # Wrap this fragment in a unique scoped div and rewrite all its <style>
    # selectors so they only apply inside that div.  This prevents global
    # `section{}`, `body{}`, `.container{}` rules in one section from
    # clobbering every other section on the page — regardless of what Gemini
    # emits.  Marker substitution has already run so image URLs are preserved.
    section_id = f"pulpo-sec-{session['section_count'] + 1}"
    fragment = namespace_fragment(fragment, section_id)
    log.info("Section %d namespaced under #%s", session["section_count"] + 1, section_id)

    # ── Assemble full HTML ────────────────────────────────────────────────
    session["sections_html"] += fragment
    session["html"] = (
        session["scaffold_open"] + session["sections_html"] + session["scaffold_close"]
    )

    # Final safety sweep: substitute ANY known markers anywhere in assembled HTML
    # (catches markers Gemini placed in scaffold/head/style blocks outside the sentinel fragment)
    for marker_index, url in session["image_urls"].items():
        placeholder = f"__IMG_{marker_index}__"
        session["html"] = session["html"].replace(placeholder, url)
    leftover_global = re.findall(r"__IMG_\d+__", session["html"])
    if leftover_global:
        log.warning("Unfilled markers remain in assembled HTML: %s", leftover_global)

    session["image_count"] = base_index + len(new_images)
    session["section_count"] += 1

    return {
        "section_count": session["section_count"],
        "image_count": session["image_count"],
        "new_images": len(new_images),
        "html_bytes": len(session["html"]),
        "unfilled_placeholders": leftover,
    }


# ---------------------------------------------------------------------------
# In-memory session store
# ---------------------------------------------------------------------------

class Workspace:
    def __init__(self):
        self.sessions: dict[str, dict] = {}

    def create(self) -> str:
        sid = uuid.uuid4().hex
        self.sessions[sid] = create_session_state()
        return sid

    def get(self, sid: str) -> dict:
        if sid not in self.sessions:
            raise HTTPException(status_code=404, detail="Session not found")
        return self.sessions[sid]

    def drop(self, sid: str) -> None:
        self.sessions.pop(sid, None)


workspace = Workspace()


# ---------------------------------------------------------------------------
# FastAPI routes
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def root():
    if FRONTEND_INDEX.exists():
        return FileResponse(FRONTEND_INDEX)
    return HTMLResponse("<h1>Frontend not found</h1><p>Expected at frontend/index.html</p>", status_code=404)


@app.post("/api/session/new")
async def session_new():
    sid = workspace.create()
    return {"session_id": sid}


@app.delete("/api/session/{sid}")
async def session_delete(sid: str):
    workspace.drop(sid)
    return {"status": "deleted"}


@app.get("/api/session/{sid}/html")
async def session_html(sid: str):
    s = workspace.get(sid)
    return {
        "html": s["html"],
        "section_count": s["section_count"],
        "image_count": s["image_count"],
    }


@app.get("/api/session/{sid}/preview", response_class=HTMLResponse)
async def session_preview(sid: str):
    s = workspace.get(sid)
    if not s["html"]:
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;padding:40px;color:#666'>"
            "<p>Sube tu primera sección para ver la preview.</p></body></html>"
        )
    return HTMLResponse(s["html"])


@app.get("/api/session/{sid}/export")
async def session_export(sid: str):
    s = workspace.get(sid)
    if not s["html"]:
        raise HTTPException(status_code=400, detail="Nothing to export yet")
    return Response(
        content=s["html"],
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="landing_{sid[:8]}.html"'},
    )


@app.post("/api/session/{sid}/section")
async def session_add_section(
    sid: str,
    image: UploadFile = File(...),
    brand_prompt: str = Form(""),
):
    s = workspace.get(sid)
    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty image upload")

    log.info("Session %s: adding section #%s (image=%s bytes, brand=%r)",
             sid[:8], s["section_count"] + 1, len(image_bytes), brand_prompt[:60])

    try:
        return await process_section(s, image_bytes, brand_prompt, _executor)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


# ---------------------------------------------------------------------------
# Clone Job API  —  POST /api/clone  +  GET /api/clone/{job_id}
# ---------------------------------------------------------------------------

# Error-code → HTTP status mapping
_ERROR_CODE_TO_STATUS: dict[str, int] = {
    "HTTP_404":   404,
    "HTTP_4XX":   422,
    "HTTP_5XX":   502,
    "REDIRECT":   422,
    "CAPTCHA":    422,
    "BLOCKED":    422,
    "NAV_TIMEOUT": 504,
    "NO_SECTIONS": 422,
    "UNKNOWN":    500,
}

# In-memory job store  {job_id -> job_dict}
_clone_jobs: dict[str, dict] = {}


class CloneRequest(BaseModel):
    url: str
    bucketName: str
    folderName: Optional[str] = None   # defaults to task UUID when omitted


def _run_clone_blocking(url: str, bucket: str, folder: Optional[str]) -> dict:
    """Run the async clone pipeline on a dedicated event loop in a worker thread.

    On Windows, Playwright launches the browser as a subprocess, which requires
    asyncio's ProactorEventLoop. Uvicorn installs a SelectorEventLoop (no
    subprocess support → NotImplementedError), so we cannot run Playwright on the
    request loop. Instead we create our own loop here (Proactor on Windows) and
    drive the whole pipeline to completion synchronously from this thread.
    """
    from backend.scraper_del_agente_de_escaneo import clonar_landing_completa

    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(
            clonar_landing_completa(url=url, bucket=bucket, folder=folder, headless=True)
        )
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            asyncio.set_event_loop(None)
            loop.close()


async def _run_clone_job(job_id: str, url: str, bucket: str, folder: Optional[str]) -> None:
    """Background task: runs the full clone pipeline and writes result into _clone_jobs."""
    # Lazy import avoids circular dependency (scraper imports backend.main lazily too)
    from backend.scraper_del_agente_de_escaneo import ScrapeError

    _clone_jobs[job_id]["status"] = "running"
    log.info("Clone job %s started — url=%s bucket=%s folder=%s", job_id[:8], url, bucket, folder)

    loop = asyncio.get_running_loop()
    try:
        # Offload to a worker thread with its own Proactor loop (Playwright needs it on Windows).
        result = await loop.run_in_executor(
            _clone_executor,
            functools.partial(_run_clone_blocking, url, bucket, folder),
        )
        _clone_jobs[job_id].update({
            "status": "completed",
            **result,          # new_clone_url, cloned_sections, imaged_generated
        })
        log.info("Clone job %s completed — url=%s", job_id[:8], result.get("new_clone_url"))

    except ScrapeError as e:
        log.warning("Clone job %s scrape error [%s]: %s", job_id[:8], e.error_code, e.message)
        _clone_jobs[job_id].update({
            "status": "failed",
            "message": e.message,
            "error_code": e.error_code,
        })
    except Exception as e:
        log.exception("Clone job %s unexpected failure", job_id[:8])
        _clone_jobs[job_id].update({
            "status": "failed",
            "message": str(e),
            "error_code": "UNKNOWN",
        })


@app.post("/api/clone", status_code=202)
async def clone_start(req: CloneRequest):
    """Start an async clone job. Returns immediately with a job_id to poll."""
    job_id = uuid.uuid4().hex
    _clone_jobs[job_id] = {"status": "pending"}
    asyncio.create_task(
        _run_clone_job(job_id, req.url, req.bucketName, req.folderName)
    )
    log.info("Clone job %s queued — url=%s bucket=%s", job_id[:8], req.url, req.bucketName)
    return {"job_id": job_id, "status": "pending"}


@app.get("/api/clone/{job_id}")
async def clone_status(job_id: str):
    """Poll the status of a clone job."""
    job = _clone_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    status = job["status"]

    if status in ("pending", "running"):
        return {"job_id": job_id, "status": status}

    if status == "completed":
        return {
            "job_id":           job_id,
            "status":           "completed",
            "new_clone_url":    job["new_clone_url"],
            "cloned_sections":  job["cloned_sections"],
            "imaged_generated": job["imaged_generated"],
        }

    # failed
    http_status = _ERROR_CODE_TO_STATUS.get(job.get("error_code", "UNKNOWN"), 500)
    return JSONResponse(
        status_code=http_status,
        content={
            "job_id":     job_id,
            "status":     "failed",
            "message":    job.get("message", "Unknown error"),
            "error_code": job.get("error_code", "UNKNOWN"),
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=5007, reload=True)
