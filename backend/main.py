"""FastAPI app: orchestrates the section-by-section landing-page build.

Single-user PoC — sessions live in memory and reset on restart.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response

load_dotenv()

from backend.gemini_openai import GeminiSession, build_client  # noqa: E402
from backend.s3 import upload_image  # noqa: E402

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
_executor = ThreadPoolExecutor(max_workers=8)

ROOT = Path(__file__).parent.parent
FRONTEND_INDEX = ROOT / "frontend" / "index.html"


class Workspace:
    def __init__(self):
        self.sessions: dict[str, dict] = {}

    def create(self) -> str:
        sid = uuid.uuid4().hex
        self.sessions[sid] = {
            "gemini": GeminiSession(_gemini_client),
            "html": "",
            "image_count": 0,
            "section_count": 0,
            "image_urls": {},       # marker_index (int) -> S3 URL
            "scaffold_open": "",    # everything before <!-- SECTION_START --> in section 1
            "scaffold_close": "",   # everything after <!-- SECTION_END --> in section 1
            "sections_html": "",    # accumulated fragment content between sentinels
        }
        return sid

    def get(self, sid: str) -> dict:
        if sid not in self.sessions:
            raise HTTPException(status_code=404, detail="Session not found")
        return self.sessions[sid]

    def drop(self, sid: str) -> None:
        self.sessions.pop(sid, None)


workspace = Workspace()


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

    loop = asyncio.get_running_loop()
    try:
        section = await loop.run_in_executor(
            _executor,
            functools.partial(
                s["gemini"].add_section,
                image_bytes,
                brand_prompt,
                s["image_count"],   # next_marker_index
            ),
        )
    except Exception as e:
        log.exception("Gemini text call failed")
        raise HTTPException(status_code=502, detail=f"Gemini text generation failed: {e}")

    new_images = section.image_prompts
    base_index = s["image_count"]
    is_first_section = s["section_count"] == 0

    # Extract section fragment using sentinel comments
    START_SENTINEL = "<!-- SECTION_START -->"
    END_SENTINEL = "<!-- SECTION_END -->"
    raw_html = section.html

    if START_SENTINEL in raw_html and END_SENTINEL in raw_html:
        start_idx = raw_html.index(START_SENTINEL)
        end_idx = raw_html.index(END_SENTINEL)
        fragment = raw_html[start_idx + len(START_SENTINEL):end_idx]
        if is_first_section:
            s["scaffold_open"] = raw_html[:start_idx]
            s["scaffold_close"] = raw_html[end_idx + len(END_SENTINEL):]
            log.info("Scaffold captured: open=%d chars, close=%d chars",
                     len(s["scaffold_open"]), len(s["scaffold_close"]))
    else:
        log.warning("Sentinel comments missing — using full response as fragment for section %d",
                    s["section_count"] + 1)
        fragment = raw_html
        if is_first_section:
            s["scaffold_open"] = ""
            s["scaffold_close"] = ""

    # Diagnostic logging
    log.info("=== DIAGNOSTIC: Section #%d ===", s["section_count"] + 1)
    for offset, spec in enumerate(new_images):
        prompt_preview = spec["prompt"][:150].replace("\n", " ")
        log.info("  Image[%d] (marker __IMG_%d__): aspect_ratio=%s | prompt=%s...",
                 offset, base_index + offset, spec.get("aspect_ratio", "unknown"), prompt_preview)
    img_count = fragment.count("<img")
    markers_in_fragment = re.findall(r"__IMG_\d+__", fragment)
    unique_markers = list(dict.fromkeys(markers_in_fragment))
    log.info("  Fragment: %d <img> tags, %d unique markers: %s", img_count, len(unique_markers), unique_markers)
    if len(unique_markers) != len(new_images):
        log.warning("  ⚠ SPEC/MARKER MISMATCH: %d specs vs %d unique markers", len(new_images), len(unique_markers))
    log.info("=== END DIAGNOSTIC ===")

    log.info("Section returned %d new image specs (base index=%d)", len(new_images), base_index)

    if new_images:
        async def _gen_and_upload(offset: int, spec: dict) -> tuple[int, str]:
            img_bytes = await loop.run_in_executor(
                _executor,
                s["gemini"].generate_image,
                spec["prompt"],
                spec.get("aspect_ratio", "1:1"),
            )
            url = await loop.run_in_executor(_executor, upload_image, img_bytes, "png")
            return base_index + offset, url

        try:
            results = await asyncio.gather(
                *[_gen_and_upload(i, spec) for i, spec in enumerate(new_images)]
            )
        except Exception as e:
            log.exception("Image generation/upload failed")
            raise HTTPException(status_code=502, detail=f"Image pipeline failed: {e}")

        # Persist new URLs
        for marker_index, url in results:
            s["image_urls"][marker_index] = url

        # Substitute only this section's markers in the fragment
        for marker_index in range(base_index, base_index + len(new_images)):
            if marker_index in s["image_urls"]:
                placeholder = f"__IMG_{marker_index}__"
                fragment = fragment.replace(placeholder, s["image_urls"][marker_index])

    leftover = re.findall(r"__IMG_\d+__", fragment)
    if leftover:
        log.warning("Unfilled placeholders remain in fragment: %s", leftover)

    # Append fragment and rebuild full HTML from scaffold + all fragments
    s["sections_html"] += fragment
    s["html"] = s["scaffold_open"] + s["sections_html"] + s["scaffold_close"]
    s["image_count"] = base_index + len(new_images)
    s["section_count"] += 1

    return {
        "section_count": s["section_count"],
        "image_count": s["image_count"],
        "new_images": len(new_images),
        "html_bytes": len(s["html"]),
        "unfilled_placeholders": leftover,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)
