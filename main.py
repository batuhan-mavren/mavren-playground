"""
Mavren Playground — Web UI for testing Mavren Brain.

A lightweight FastAPI app that:
  1. Serves a static frontend for uploading creatives and tweaking parameters
  2. Proxies analysis requests to the Mavren Brain production API
  3. Synthesizes actionable improvement suggestions via Claude VLM
  4. Protects everything behind a simple password gate
"""

import base64
import json
import logging
import os
import uuid
from io import BytesIO
from pathlib import Path
from typing import Optional

import httpx
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from sheets import append_row

# Directory for uploaded creative images (served publicly)
UPLOADS_DIR = Path("uploads")
UPLOADS_DIR.mkdir(exist_ok=True)

logger = logging.getLogger("mavren.playground")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MAVREN_API_URL = os.getenv(
    "MAVREN_API_URL", "https://mavren-brain-production.up.railway.app"
)
MAVREN_API_KEY = os.getenv("MAVREN_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
PLAYGROUND_PASSWORD = os.getenv("PLAYGROUND_PASSWORD", "mavren2026")
SESSION_COOKIE = "mavren_playground_session"
SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me-in-prod")


# ---------------------------------------------------------------------------
# Password Middleware
# ---------------------------------------------------------------------------

class PasswordAuthMiddleware(BaseHTTPMiddleware):
    """Simple cookie-based password gate."""

    async def dispatch(self, request: Request, call_next):
        # Always allow: login page, login action, static assets, health
        path = request.url.path
        if path in ("/login", "/api/login", "/health") or path.startswith("/static"):
            return await call_next(request)

        # Check session cookie
        session = request.cookies.get(SESSION_COOKIE)
        if session == SESSION_SECRET:
            return await call_next(request)

        # Not authenticated → redirect to login
        if path == "/" or not path.startswith("/api"):
            return RedirectResponse("/login")
        else:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Mavren Playground", docs_url=None, redoc_url=None)
app.add_middleware(PasswordAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Auth Routes
# ---------------------------------------------------------------------------

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mavren Playground — Login</title>
<link rel="icon" href="/static/logo-mark-blue.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&family=DM+Sans:wght@400;500;600&display=swap">
<style>
  :root{
    --mavren-blue:#5271FF;--vivid-azure:#0175E4;--mauve:#7E88F3;
    --dark-blue:#11245B;--slate:#707B8A;--cloud:#F4F6FA;
  }
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:'DM Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
       background:var(--cloud);color:var(--dark-blue);
       display:flex;align-items:center;justify-content:center;min-height:100vh;
       -webkit-font-smoothing:antialiased;
       background-image:
         radial-gradient(700px 460px at 100% -10%,rgba(82,113,255,0.12),transparent 60%),
         radial-gradient(600px 420px at 0% 110%,rgba(126,136,243,0.10),transparent 60%);
       background-attachment:fixed}
  .login-card{background:#fff;border:1px solid #E2E6F1;border-radius:20px;
              padding:44px 40px 40px;width:100%;max-width:420px;text-align:center;
              box-shadow:0 20px 50px rgba(17,36,91,0.10),0 4px 12px rgba(17,36,91,0.04)}
  .logo-wrap{width:64px;height:64px;border-radius:16px;background:var(--cloud);
             border:1px solid #E2E6F1;display:flex;align-items:center;justify-content:center;
             margin:0 auto 22px}
  .logo-wrap img{width:46px;height:46px;display:block}
  .login-card h1{font-family:'Poppins',sans-serif;font-size:24px;font-weight:700;
                 letter-spacing:-0.015em;margin-bottom:6px;color:var(--dark-blue)}
  .login-card h1 .accent{background:linear-gradient(135deg,#a076f9 0%,#3c6ae7 100%);
                 -webkit-background-clip:text;-webkit-text-fill-color:transparent;
                 background-clip:text}
  .login-card p{color:var(--slate);font-size:13.5px;margin-bottom:28px;font-weight:500}
  input[type=password]{width:100%;padding:13px 16px;border-radius:11px;
       border:1px solid #C9D0E2;background:#fff;color:var(--dark-blue);
       font-size:15px;outline:none;font-family:'DM Sans',sans-serif;
       transition:border-color .15s,box-shadow .15s}
  input[type=password]::placeholder{color:var(--slate);opacity:.7}
  input[type=password]:focus{border-color:var(--mavren-blue);
       box-shadow:0 0 0 3px rgba(82,113,255,0.15)}
  button{width:100%;margin-top:14px;padding:13px;border:none;border-radius:11px;
         background:linear-gradient(135deg,#a076f9 0%,#3c6ae7 100%);color:#fff;
         font-size:15px;font-weight:600;font-family:'Poppins',sans-serif;
         letter-spacing:.01em;cursor:pointer;
         box-shadow:0 4px 14px rgba(60,106,231,0.30);
         transition:transform .15s,box-shadow .15s}
  button:hover{transform:translateY(-1px);box-shadow:0 6px 20px rgba(60,106,231,0.40)}
  .error{color:#F46531;font-size:13px;margin-top:12px;display:none;font-weight:500}
  .footer{margin-top:24px;font-size:11.5px;color:var(--slate);font-style:italic}
</style>
</head>
<body>
<div class="login-card">
  <div class="logo-wrap"><img src="/static/logo-mark-blue.svg" alt="Mavren"></div>
  <h1><span class="accent">Mavren</span> Playground</h1>
  <p>Cognitive creative analysis · 7-layer engine</p>
  <form id="loginForm">
    <input type="password" id="pw" placeholder="Password" autofocus>
    <button type="submit">Enter</button>
    <div class="error" id="err">Wrong password</div>
  </form>
  <div class="footer">Behind every metric, there's a human decision.</div>
</div>
<script>
document.getElementById('loginForm').addEventListener('submit',async e=>{
  e.preventDefault();
  const r=await fetch('/api/login',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({password:document.getElementById('pw').value})});
  if(r.ok){window.location.href='/'}
  else{document.getElementById('err').style.display='block'}
});
</script>
</body></html>"""


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return LOGIN_HTML


@app.post("/api/login")
async def api_login(request: Request):
    body = await request.json()
    if body.get("password") == PLAYGROUND_PASSWORD:
        resp = JSONResponse({"ok": True})
        resp.set_cookie(
            SESSION_COOKIE,
            SESSION_SECRET,
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 30,  # 30 days
        )
        return resp
    raise HTTPException(401, "Wrong password")


@app.post("/api/logout")
async def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ---------------------------------------------------------------------------
# Mavren Brain Proxy
# ---------------------------------------------------------------------------

@app.post("/api/analyze")
async def analyze(
    image: UploadFile = File(...),
    channel: str = Form("paid_social"),
    funnel_stage: str = Form("prospecting"),
    objective: str = Form("click"),
    audience_mood: Optional[str] = Form(None),
    brand_archetype: Optional[str] = Form(None),
    segment_id: Optional[str] = Form(None),
    region: Optional[str] = Form(None),
    company_url: Optional[str] = Form(None),
    test_month: Optional[int] = Form(None),
):
    """Proxy the image + params to Mavren Brain's /affective/profile-from-image."""

    image_bytes = await image.read()

    # Build multipart form data
    files = {"image": (image.filename, image_bytes, image.content_type or "image/png")}
    data = {
        "channel": channel,
        "funnel_stage": funnel_stage,
        "objective": objective,
    }
    if audience_mood:
        data["audience_mood"] = audience_mood
    if brand_archetype:
        data["brand_archetype"] = brand_archetype
    if segment_id:
        data["segment_id"] = segment_id
    if region:
        data["region"] = region
    if company_url:
        data["company_url"] = company_url
    if test_month is not None:
        data["test_month"] = str(test_month)

    headers = {}
    if MAVREN_API_KEY:
        headers["X-API-Key"] = MAVREN_API_KEY

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{MAVREN_API_URL}/affective/profile-from-image",
                files=files,
                data=data,
                headers=headers,
            )
        if resp.status_code != 200:
            return JSONResponse(
                {"error": f"Mavren Brain returned {resp.status_code}", "detail": resp.text},
                status_code=resp.status_code,
            )
        return resp.json()
    except httpx.TimeoutException:
        return JSONResponse({"error": "Mavren Brain request timed out (120s)"}, status_code=504)
    except Exception as e:
        logger.exception("Analyze proxy failed")
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Video Analysis Proxy
# ---------------------------------------------------------------------------

MAX_VIDEO_SIZE_MB = 50
ALLOWED_VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/webm"}

@app.post("/api/analyze-video")
async def analyze_video(
    video: UploadFile = File(...),
    channel: str = Form("paid_social"),
    funnel_stage: str = Form("prospecting"),
    objective: str = Form("click"),
    audience_mood: Optional[str] = Form(None),
    brand_archetype: Optional[str] = Form(None),
    segment_id: Optional[str] = Form(None),
    region: Optional[str] = Form(None),
    attention_context: Optional[str] = Form(None),
):
    """Proxy video + params to Mavren Brain's /affective/profile-from-video."""

    video_bytes = await video.read()

    if len(video_bytes) > MAX_VIDEO_SIZE_MB * 1024 * 1024:
        return JSONResponse(
            {"error": f"Video exceeds {MAX_VIDEO_SIZE_MB}MB limit"},
            status_code=413,
        )

    # Build multipart form data
    content_type = video.content_type or "video/mp4"
    files = {"video": (video.filename, video_bytes, content_type)}
    data = {
        "channel": channel,
        "funnel_stage": funnel_stage,
        "objective": objective,
    }
    if audience_mood:
        data["audience_mood"] = audience_mood
    if brand_archetype:
        data["brand_archetype"] = brand_archetype
    if segment_id:
        data["segment_id"] = segment_id
    if region:
        data["region"] = region
    if attention_context:
        data["attention_context"] = attention_context

    headers = {}
    if MAVREN_API_KEY:
        headers["X-API-Key"] = MAVREN_API_KEY

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                f"{MAVREN_API_URL}/affective/profile-from-video",
                files=files,
                data=data,
                headers=headers,
            )
        if resp.status_code != 200:
            return JSONResponse(
                {"error": f"Mavren Brain returned {resp.status_code}", "detail": resp.text},
                status_code=resp.status_code,
            )
        return resp.json()
    except httpx.TimeoutException:
        return JSONResponse({"error": "Video analysis timed out (180s)"}, status_code=504)
    except Exception as e:
        logger.exception("Video analyze proxy failed")
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Creative Regeneration Proxy
# ---------------------------------------------------------------------------

@app.post("/api/regenerate")
async def regenerate(
    image: UploadFile = File(...),
    channel: str = Form("paid_social"),
    funnel_stage: str = Form("prospecting"),
    objective: str = Form("click"),
    audience_mood: Optional[str] = Form(None),
    brand_archetype: Optional[str] = Form(None),
    segment_id: Optional[str] = Form(None),
    region: Optional[str] = Form(None),
    attention_context: Optional[str] = Form(None),
):
    """Proxy to Mavren Brain's /regenerate endpoint — edits original image + generates copy."""

    image_bytes = await image.read()

    files = {"image": (image.filename, image_bytes, image.content_type or "image/png")}
    data = {
        "channel": channel,
        "funnel_stage": funnel_stage,
        "objective": objective,
    }
    if audience_mood:
        data["audience_mood"] = audience_mood
    if brand_archetype:
        data["brand_archetype"] = brand_archetype
    if segment_id:
        data["segment_id"] = segment_id
    if region:
        data["region"] = region
    if attention_context:
        data["attention_context"] = attention_context

    headers = {}
    if MAVREN_API_KEY:
        headers["X-API-Key"] = MAVREN_API_KEY

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                f"{MAVREN_API_URL}/regenerate",
                files=files,
                data=data,
                headers=headers,
            )
        if resp.status_code != 200:
            return JSONResponse(
                {"error": f"Mavren Brain returned {resp.status_code}", "detail": resp.text},
                status_code=resp.status_code,
            )
        return resp.json()
    except httpx.TimeoutException:
        return JSONResponse({"error": "Regeneration timed out (180s)"}, status_code=504)
    except Exception as e:
        logger.exception("Regenerate proxy failed")
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Claude Synthesis — Improvement Suggestions
# ---------------------------------------------------------------------------

SYNTHESIS_PROMPT_DIGITAL = """You are Mavren Brain's creative strategist. You've just analyzed an advertising creative through a 7-layer psychological engine. Below is the full raw analysis.

Your job: synthesize this into **clear, actionable improvement suggestions** that a creative team or media buyer can immediately act on.

Structure your response as:

## Overall Assessment
One paragraph: what this creative does well psychologically and where it falls short.

## Key Strengths
2-3 bullet points of what's working (with the psychological WHY).

## Improvement Recommendations
3-5 specific, actionable recommendations. Each should include:
- **What to change** (concrete, specific)
- **Why it matters** (which psychological layer benefits)
- **Expected impact** (what shifts in the viewer's mind)

## Emotional Strategy Note
One paragraph on whether the emotional strategy (the emotion being triggered, the funnel stage, the audience) is well-aligned — and what the ideal emotional path would be.

If this is a VIDEO creative, also include:

## Emotional Arc Review
One paragraph analyzing the video's emotional journey: does the arc (build, resolve, sustain, oscillate, decline) serve the campaign objective? Is the peak-end moment (last frame) optimized for memory and action? Any pacing issues?

Keep it sharp, strategic, and grounded in the data. No fluff. Write for someone who understands marketing but not cognitive science — translate the psychology into business language.

---

RAW ANALYSIS:
{raw_response}"""


SYNTHESIS_PROMPT_OOH = """You are Mavren Brain's creative strategist analysing an Out-of-Home / transit / DOOH creative through a 7-layer psychological engine. Below is the full raw analysis.

This is **not a digital ad**. The viewer encounters this creative at speed, at distance, in physical context — not in a feed. Discard scroll-stop, click-through, and conversion-rate framing entirely. The success metric here is **brand memory and recall** that triggers action at the next moment of opportunity, not immediate engagement.

Channel: {channel}{placement_block}

Structure your response as:

## Overall Assessment
One paragraph: how does this creative perform on the **glance test** (3 seconds at 30+ km/h, or seconds of distracted dwell on a platform)? Does it carry one clear idea, or does it ask the viewer to do too much cognitive work for the surface?

## Key Strengths
2-3 bullet points of what's working — focus on **visual hierarchy at distance**, **single-message discipline**, **brand identifiability without copy**, and **emotional residue** (what feeling the viewer carries away after the encounter).

## Improvement Recommendations
3-5 concrete recommendations. Each should include:
- **What to change** (specific to this surface — text-density caps, focal-point placement, contrast at distance, dwell-window match, daypart fit)
- **Why it matters** (which psychological/perceptual constraint of *this* OOH surface drives it — viewer speed, viewing angle, frequency cycle, environmental noise)
- **Expected impact** (what shifts in the viewer's *memory trace* — not their click behaviour)

Avoid CTA-heavy advice unless the surface genuinely supports interaction (DOOH-with-QR, taxi-interior screen). For static OOH, optimise for recall, not response.

## Frequency & Daypart Fit
One paragraph: given a typical commuter sees this creative 5-15× per week on this surface, does the asset reward repeat exposure or fatigue against it? Which dayparts (AM rush / midday / PM rush / weekend) does the emotional register match — and which does it miss?

## Per-Surface Repurposing
One paragraph: if this exact creative were redeployed to a *different* Moove surface in the network, which surfaces would it survive on as-is, which would need a brief change, and which should not run it at all? Reference the channel_fit ranking in the analysis.

If this is a VIDEO creative (DOOH or concept-train), also include:

## Emotional Arc Review
One paragraph: for a digital screen with looping playback (typically 6-15 seconds), does the arc (build, resolve, sustain, oscillate, decline) serve recall? Is the **last frame** — the peak-end the viewer carries away — branded and unambiguous? Any pacing issues for a viewer who joined mid-loop?

Keep it sharp, OOH-fluent, grounded in the data. Write for a media planner or OOH brief, not a paid-social buyer. Don't translate digital advice into OOH language — start from OOH first principles.

---

RAW ANALYSIS:
{raw_response}"""


# Channel families that get the OOH-flavored synthesis prompt.
# Anything matching these prefixes/values uses SYNTHESIS_PROMPT_OOH.
_OOH_CHANNEL_PREFIXES = ("transit_", "dooh_")
_OOH_CHANNEL_EXACT = {"ooh"}


def _is_ooh_channel(channel: Optional[str]) -> bool:
    if not channel:
        return False
    if channel in _OOH_CHANNEL_EXACT:
        return True
    return channel.startswith(_OOH_CHANNEL_PREFIXES)


@app.post("/api/synthesize")
async def synthesize(request: Request):
    """Send the raw Mavren Brain response to Claude for human-friendly synthesis."""

    if not ANTHROPIC_API_KEY:
        return JSONResponse(
            {"error": "ANTHROPIC_API_KEY not configured — synthesis unavailable"},
            status_code=503,
        )

    body = await request.json()
    raw_response = body.get("raw_response", {})
    channel = body.get("channel") or ""
    placement_label = body.get("placement_label") or ""

    if _is_ooh_channel(channel):
        placement_block = (
            f"\nPlacement context: {placement_label}\n"
            "Use the placement context to anchor recommendations to that audience and physical environment."
            if placement_label
            else ""
        )
        prompt_text = SYNTHESIS_PROMPT_OOH.format(
            channel=channel,
            placement_block=placement_block,
            raw_response=json.dumps(raw_response, indent=2, default=str),
        )
    else:
        prompt_text = SYNTHESIS_PROMPT_DIGITAL.format(
            raw_response=json.dumps(raw_response, indent=2, default=str)
        )

    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": prompt_text}],
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if resp.status_code != 200:
            return JSONResponse(
                {"error": f"Claude API returned {resp.status_code}", "detail": resp.text},
                status_code=502,
            )
        data = resp.json()
        text = data["content"][0]["text"]
        return {"synthesis": text}
    except Exception as e:
        logger.exception("Synthesis failed")
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Google Sheets Logging
# ---------------------------------------------------------------------------

@app.post("/api/log")
async def log_to_sheet(request: Request, background_tasks: BackgroundTasks):
    """
    Log a completed analysis to Google Sheets.

    Called by the frontend after both analysis and synthesis are done.
    Saves the image locally and appends a row with a public URL to the sheet.
    """
    body = await request.json()

    image_b64 = body.get("image_base64", "")
    image_filename = body.get("image_filename", "creative.png")
    channel = body.get("channel", "—")
    funnel_stage = body.get("funnel_stage", "—")
    region = body.get("region")
    raw_response = body.get("raw_response", {})
    synthesis = body.get("synthesis")

    # Save image locally and build a public URL
    image_link = None
    if image_b64:
        try:
            image_bytes = base64.b64decode(image_b64)
            ext = Path(image_filename).suffix or ".png"
            unique_name = f"{uuid.uuid4().hex}{ext}"
            (UPLOADS_DIR / unique_name).write_bytes(image_bytes)

            # Build the public URL from the request's base
            base_url = str(request.base_url).rstrip("/")
            image_link = f"{base_url}/uploads/{unique_name}"
        except Exception as e:
            logger.warning("Image save failed: %s", e)

    def _do_log():
        append_row(
            image_link=image_link,
            channel=channel,
            funnel_stage=funnel_stage,
            region=region,
            raw_response=raw_response,
            synthesis=synthesis,
        )

    background_tasks.add_task(_do_log)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "service": "mavren-playground"}


# ---------------------------------------------------------------------------
# Serve Frontend
# ---------------------------------------------------------------------------

# Serve uploaded images publicly (for Google Sheets links)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# Serve static files (CSS, JS if we split later)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    with open("static/index.html") as f:
        return f.read()
