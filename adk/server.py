"""live-dj on ADK — the Gemini Live backend, the framework way (EP2).

The SAME radio as EP1 (live-dj), rebuilt with Google ADK instead of the raw
google-genai SDK. What the framework does for you, vs EP1's `raw_server.py`:

  - the two hand-rolled asyncio tasks  -> LiveRequestQueue (up) + runner.run_live() (down)
  - the per-turn `while True: session.receive()` loop  -> GONE (run_live is continuous)
  - tool declarations + manual send_tool_response       -> plain Python functions; ADK runs them

A tool returns to the MODEL, not the browser — so the music "play" command is
emitted *here*, in the run_live loop, when we see the function_call (the one
real design call of the rewrite). Everything the browser sees is byte-for-byte
the EP1 protocol, so the frontend is shared with EP1, byte for byte.
"""
import asyncio
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from google.genai import types
from google.adk.runners import Runner, RunConfig
from google.adk.agents.run_config import StreamingMode
from google.adk.agents import LiveRequestQueue
from google.adk.sessions import DatabaseSessionService

from adk.agent import root_agent
from adk.tools import to_play_command
from adk.youtube_tools import to_youtube_command
from adk import auth

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("jarvis-adk")

VOICE = os.getenv("LIVE_VOICE", "Charon")
APP_NAME = "jarvis-adk"

# Single owner, single continuous conversation — this IS the persistent memory.
# Every websocket connection (today, tomorrow, after a server restart) resumes
# the SAME session_id, so ADK's DatabaseSessionService loads the full prior
# conversation as context instead of starting blank. DB_URL defaults to a local
# sqlite file; point it at Postgres/MySQL for anything beyond a single box.
MEMORY_USER_ID = "owner"
MEMORY_SESSION_ID = "main"
DB_URL = os.getenv("DB_URL", "sqlite+aiosqlite:///./jarvis_memory.db")

session_service = DatabaseSessionService(db_url=DB_URL)
runner = Runner(app_name=APP_NAME, agent=root_agent, session_service=session_service)

RUN_CONFIG = RunConfig(
    streaming_mode=StreamingMode.BIDI,                 # full-duplex live
    response_modalities=["AUDIO"],
    speech_config=types.SpeechConfig(                  # Mira's native voice (EP1 parity)
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE)
        )
    ),
    input_audio_transcription=types.AudioTranscriptionConfig(),
    output_audio_transcription=types.AudioTranscriptionConfig(),
)

app = FastAPI(title="jarvis-adk")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
FRONTEND = Path(__file__).resolve().parents[1] / "frontend_jarvis"
ASSETS = Path(__file__).resolve().parents[1] / "assets"

_PUBLIC_PATHS = {"/login", "/healthz"}


class AuthMiddleware(BaseHTTPMiddleware):
    """Gate every HTTP route except /login behind the signed session cookie.

    The websocket handler checks the same cookie itself (see ws() below) —
    middleware doesn't run for websocket upgrades in Starlette.
    """
    async def dispatch(self, request: Request, call_next):
        if not auth.configured():
            # Fail closed with a clear message rather than silently running unprotected —
            # this matters a lot more here than in the original demo, since this build can
            # execute shell commands on the host.
            return HTMLResponse(
                "JARVIS is not configured: set JARVIS_PASSWORD and SESSION_SECRET in .env "
                "before running this where anyone else could reach it.",
                status_code=500,
            )
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)
        if not auth.verify_session_cookie(request.cookies.get(auth.COOKIE_NAME)):
            return RedirectResponse("/login")
        return await call_next(request)


app.add_middleware(AuthMiddleware)


@app.get("/healthz")
async def healthz():
    # Deliberately unauthenticated — hosting platforms (Render, etc.) hit this
    # without a session cookie to check liveness. Reveals nothing sensitive.
    return {"status": "ok"}


@app.get("/login")
async def login_page():
    return HTMLResponse(auth.LOGIN_PAGE.format(error=""))


@app.post("/login")
async def login_submit(username: str = Form(...), password: str = Form(...)):
    if not auth.check_credentials(username, password):
        return HTMLResponse(
            auth.LOGIN_PAGE.format(error='<p class="err">wrong username or password</p>'),
            status_code=401,
        )
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        auth.COOKIE_NAME, auth.make_session_cookie(username),
        httponly=True, samesite="lax", secure=auth.COOKIE_SECURE, max_age=auth.SESSION_TTL_SECONDS,
    )
    return resp


@app.get("/logout")
async def logout():
    resp = RedirectResponse("/login")
    resp.delete_cookie(auth.COOKIE_NAME)
    return resp


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    if not auth.configured() or not auth.verify_session_cookie(websocket.cookies.get(auth.COOKIE_NAME)):
        await websocket.close(code=4401)  # policy violation / unauthorized
        return
    await websocket.accept()
    log.info("ws connected; opening ADK live session (model=%s, voice=%s)", root_agent.model, VOICE)
    # Resume the one persistent session if it exists, else create it the first time.
    # This is what makes memory survive a browser refresh, a reconnect, or a server
    # restart — ADK reloads that session's full event history from DB_URL as context.
    # get_session's exact "not found" behavior (None vs. raising) has varied across
    # ADK versions, so treat either as "doesn't exist yet" and create it.
    try:
        session = await session_service.get_session(
            app_name=APP_NAME, user_id=MEMORY_USER_ID, session_id=MEMORY_SESSION_ID
        )
    except Exception:
        session = None
    if session is None:
        session = await session_service.create_session(
            app_name=APP_NAME, user_id=MEMORY_USER_ID, session_id=MEMORY_SESSION_ID
        )
    live_request_queue = LiveRequestQueue()

    async def upstream():
        # browser mic (16k PCM) -> the queue. ADK feeds the queue to Gemini.
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                log.info("upstream: browser disconnected")
                return
            raw = msg.get("bytes")
            if raw:
                live_request_queue.send_realtime(
                    types.Blob(data=raw, mime_type="audio/pcm;rate=16000")
                )

    async def handle(event):
        # mirror EP1's handle(), but the source is an ADK run_live Event.
        it = getattr(event, "input_transcription", None)
        ot = getattr(event, "output_transcription", None)
        if it and getattr(it, "text", None):
            await websocket.send_text(json.dumps({"type": "transcript", "role": "user", "text": it.text}))
        if ot and getattr(ot, "text", None):
            await websocket.send_text(json.dumps({"type": "transcript", "role": "jarvis", "text": ot.text}))
        content = getattr(event, "content", None)
        if content and getattr(content, "parts", None):
            for part in content.parts:
                idata = getattr(part, "inline_data", None)
                if idata and getattr(idata, "data", None):
                    await websocket.send_bytes(idata.data)             # 24k voice
                fc = getattr(part, "function_call", None)
                if fc:                                                 # the tool -> browser bridge
                    args = dict(getattr(fc, "args", None) or {})
                    cmd = to_play_command(fc.name, args) or to_youtube_command(fc.name, args)
                    if cmd:
                        await websocket.send_text(json.dumps(cmd if "type" in cmd else {"type": "play", **cmd}))
                    else:
                        # non-music/youtube tool (open_app, run_shell_command, search_files,
                        # get_system_status, web_search) — surface it in the transcript
                        # so it's visible when JARVIS acts, not just when it talks.
                        await websocket.send_text(json.dumps({"type": "tool_call", "name": fc.name, "args": args}))
                fr = getattr(part, "function_response", None)
                if fr:
                    # A few tools' USEFUL payload is their return value, not their call
                    # args (open_website resolves a URL server-side; youtube_search hits
                    # a real API) — relay those specific responses on to the browser too.
                    resp = dict(getattr(fr, "response", None) or {})
                    if fr.name == "open_website" and resp.get("url"):
                        await websocket.send_text(json.dumps({"type": "open_url", "url": resp["url"]}))
                    elif fr.name == "youtube_search" and resp.get("result") == "ok":
                        await websocket.send_text(json.dumps({"type": "youtube_results", "results": resp.get("results", [])}))
                    elif fr.name == "youtube_play" and resp.get("url"):
                        # youtube_play resolves a real youtube.com/watch URL server-side
                        # (see adk/youtube_tools.py) — relay it the same way open_website
                        # does, so the browser opens the ACTUAL YouTube site/app, not an
                        # embedded player on this page.
                        await websocket.send_text(json.dumps({"type": "open_url", "url": resp["url"]}))
        if getattr(event, "interrupted", None):
            await websocket.send_text(json.dumps({"type": "interrupted"}))   # barge-in

    async def downstream():
        # ONE continuous loop — no per-turn `while True`. ADK owns the turn-taking.
        async for event in runner.run_live(
            user_id=session.user_id, session_id=session.id,
            live_request_queue=live_request_queue, run_config=RUN_CONFIG,
        ):
            await handle(event)

    async def keepalive():
        # Render's free tier (and some other proxies) can drop a websocket after a
        # stretch with no traffic. A tiny ping every 20s keeps data flowing in both
        # directions — the client acks it (main.js), and that ack is itself inbound
        # traffic on this connection, which is specifically what keeps Render's
        # free-tier idle timer from firing.
        try:
            while True:
                await asyncio.sleep(20)
                await websocket.send_text(json.dumps({"type": "ping"}))
        except Exception:
            return

    up = asyncio.create_task(upstream(), name="upstream")
    down = asyncio.create_task(downstream(), name="downstream")
    ka = asyncio.create_task(keepalive(), name="keepalive")
    try:
        done, pending = await asyncio.wait({up, down, ka}, return_when=asyncio.FIRST_COMPLETED)
        live_request_queue.close()   # let run_live wind down cleanly before we cancel anything
        for t in done:
            exc = t.exception()
            if exc:
                log.exception("%s task FAILED: %r", t.get_name(), exc, exc_info=exc)
                try:
                    await websocket.send_text(json.dumps({"type": "error", "message": f"{type(exc).__name__}: {exc}"}))
                except Exception:
                    pass
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    except WebSocketDisconnect:
        log.info("ws disconnected")
    finally:
        live_request_queue.close()
        log.info("ws closed")


if ASSETS.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS)), name="assets")
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")
