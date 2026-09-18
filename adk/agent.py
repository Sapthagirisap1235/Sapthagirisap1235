"""Mira as an ADK agent (EP2).

Same persona, same tools as EP1's raw build — but expressed the framework way:
an `Agent` with plain-function tools. The Runner + LiveRequestQueue + run_live
(in server.py) replace EP1's two hand-rolled asyncio tasks and the per-turn
receive() loop.
"""
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from google.adk.agents import Agent
from google.adk.models import Gemini
from google.genai import types

from adk.jarvis_persona import build_instruction
from adk.tools import play_playlist, play_track, skip, pause
from adk.jarvis_tools import open_app, open_website, run_shell_command, search_files, get_system_status, web_search
from adk.youtube_tools import youtube_search, youtube_play, scroll_youtube_results

# Whether open_app / run_shell_command / search_files / get_system_status make
# ANY sense depends entirely on where this server runs. Locally, "this
# machine" IS the user's machine. On a remote deployment (Render, a VPS),
# "this machine" is the remote box — those tools would act on IT, not on
# whatever device the user is actually talking from, which is exactly the bug
# that prompted this flag. Default true (local dev); set LOCAL_MODE=false in
# .env for any remote deployment.
LOCAL_MODE = os.getenv("LOCAL_MODE", "true").lower() != "false"

tools = [web_search, open_website, youtube_search, youtube_play, scroll_youtube_results,
         play_playlist, play_track, skip, pause]
if LOCAL_MODE:
    tools[1:1] = [open_app, run_shell_command, search_files, get_system_status]

root_agent = Agent(
    # Gemini(...) instead of a bare model string so JARVIS's voice travels WITH the
    # agent — that way `adk web` (which builds its own RunConfig) uses the same voice too.
    model=Gemini(
        model=os.getenv("LIVE_MODEL", "gemini-3.1-flash-live-preview"),
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=os.getenv("LIVE_VOICE", "Charon")))),
    ),
    name="jarvis",
    instruction=build_instruction(local_mode=LOCAL_MODE),
    tools=tools,
)
