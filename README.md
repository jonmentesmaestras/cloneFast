# Vision-to-Code Landing Builder — PoC

Section-by-section landing page cloner. Drag a screenshot, get HTML; drag the next
screenshot, the same Gemini chat session appends it. Generated images are uploaded
to S3 and substituted into the HTML.

## Architecture (faithful-to-manual-flow PoC)

```
[ Browser UI (vanilla HTML+JS) ]
            │  POST /api/session/{id}/section  (multipart: image + brand_prompt)
            ▼
[ FastAPI ]──► GeminiSession (persistent chat, gemini-2.5-flash)
            │      └─► returns { html, images: [{prompt, aspect_ratio}, ...] }
            ├──► gemini-2.5-flash-image  per image (in parallel)
            └──► boto3 → S3              upload + URL substitution for __IMG_N__
```

One session per landing page. Sessions are in-memory and reset on restart (single-user PoC).

## Setup

```powershell
# 1. Python venv
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. Add S3 bucket to .env
#    (your .env already has GEMINI_API_KEY + AWS_* — add this one line):
#    S3_BUCKET=your-bucket-name
#
#    The bucket must allow public reads on objects we upload, OR you must
#    configure a bucket policy that makes the landing-builder/ prefix public.

# 3. Run
python -m backend.main
```

Then open <http://127.0.0.1:8000>.

## Files

- [backend/main.py](backend/main.py) — FastAPI app + session orchestration
- [backend/gemini.py](backend/gemini.py) — chat-session wrapper around google-genai
- [backend/s3.py](backend/s3.py) — boto3 uploader
- [frontend/index.html](frontend/index.html) — split-screen UI

## Known PoC limitations

- **Single user, in-memory state.** Restarting the server wipes all sessions.
- **No auth.** Don't deploy this as-is.
- **One Gemini call per section returns the FULL accumulated HTML.** This works
  while the page fits in Gemini's output window (~8k tokens of HTML). For very
  long landings, switch to the BeautifulSoup merge approach from Specs.md §4.
- **Image placeholders use `__IMG_N__` markers.** If Gemini omits a marker, the
  response will include an `unfilled_placeholders` warning.
- **Bucket must allow public reads.** Quickest setup: bucket policy granting
  `s3:GetObject` to `*` on `landing-builder/*`.

## Next milestones (per Specs.md)

1. ✅ Hito 1 — React-equivalent UI + FastAPI upload endpoint
2. ✅ Hito 2 — Gemini Vision with structured JSON output
3. ✅ Hito 3 — Image generation (gemini-2.5-flash-image) + S3 upload
4. ⏳ Hito 4 — BeautifulSoup surgical merge (replace whole-doc rewrite)
5. ⏳ User-driven edits ("change this title", "swap this image")
6. ⏳ One-click deploy to Vercel/Netlify
