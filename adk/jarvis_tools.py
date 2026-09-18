"""JARVIS's tools — computer control + live web search, as ADK function tools.

Same shape as tools.py (Mira's music tools): plain async functions, schema
read from the signature + docstring by ADK. These run FOR REAL on the host
machine, unlike Mira's, which only signal the browser — so they're written
defensively:

  - run_shell_command refuses anything that looks destructive, and always
    times out instead of hanging the live session.
  - Every tool returns a small dict quickly. A slow tool freezes the voice,
    same lesson as the music tools.
  - web_search does NOT use the Live session's own tool-calling — it makes
    a separate, ordinary (non-live) Gemini call with Google Search grounding
    enabled, and hands back a plain-text answer. The Live API doesn't let
    you mix the built-in google_search tool with custom function tools in
    the same session, so grounding one function call this way sidesteps
    that limitation entirely.
"""
import asyncio
import os
import platform
import shutil
from pathlib import Path

HOME = Path.home()

# A few sites worth resolving by name rather than guessing "name.com" — either
# because that guess would be wrong (maps, docs) or because the short name is
# what people actually say out loud. YouTube is deliberately NOT here —
# youtube_search / youtube_play (adk/youtube_tools.py) handle that instead.
_KNOWN_SITES = {
    "gmail": "https://mail.google.com",
    "google": "https://google.com",
    "google maps": "https://maps.google.com",
    "maps": "https://maps.google.com",
    "google docs": "https://docs.google.com",
    "google drive": "https://drive.google.com",
    "github": "https://github.com",
    "netflix": "https://netflix.com",
    "spotify": "https://open.spotify.com",
    "twitter": "https://twitter.com",
    "x": "https://x.com",
    "reddit": "https://reddit.com",
    "amazon": "https://amazon.com",
    "whatsapp": "https://web.whatsapp.com",
    "chatgpt": "https://chat.openai.com",
    "claude": "https://claude.ai",
}

# Substrings that make a shell command an automatic refusal, regardless of
# framing or who's asking. Not a complete blocklist — a determined user could
# get around it — but it stops the obvious footguns a voice command could
# trigger by accident ("clean up my disk" -> something copy-pasted verbatim).
_BLOCKED_SUBSTRINGS = (
    "rm -rf", "rm -fr", "mkfs", ":(){:|:&};:", "dd if=", "> /dev/", "chmod -r 777",
    "shutdown", "reboot", "halt ", "poweroff", "passwd", "sudo ", "del /f", "format ",
)


def _looks_dangerous(command: str) -> bool:
    lowered = command.lower()
    return any(bad in lowered for bad in _BLOCKED_SUBSTRINGS)


async def open_app(app_name: str) -> dict:
    """Open a program installed on the user's computer — NOT a website.

    Use this only for actual desktop applications (VS Code, Spotify's desktop app, Terminal,
    Slack, a game). If what the user asked for is a website or web app instead (YouTube,
    Gmail, Google Maps, GitHub, Netflix, etc.), use open_website instead — this tool will
    fail for those because there's no installed program by that name.

    Args:
        app_name: the application's name, e.g. "Visual Studio Code", "Spotify", "Terminal".
    """
    system = platform.system()
    try:
        if system == "Darwin":
            proc = await asyncio.create_subprocess_exec("open", "-a", app_name)
        elif system == "Windows":
            proc = await asyncio.create_subprocess_shell(f'start "" "{app_name}"')
        else:
            proc = await asyncio.create_subprocess_exec("xdg-open", app_name)
        await proc.wait()
        return {"result": "ok", "opened": app_name}
    except Exception as e:
        return {"result": "error", "message": str(e)}


async def open_website(site_or_url: str) -> dict:
    """Open a website or web app in the user's own browser (not this server's).

    Use this for anything that lives on the web — Gmail, Google Maps, GitHub, Netflix,
    a specific URL someone gives you, etc. For anything YouTube-related, prefer
    youtube_search instead — it gives an in-page, controllable results list rather than
    just handing off to a page you can't see or act on further.

    Args:
        site_or_url: a short site name (e.g. "gmail") or a full URL.
    """
    target = site_or_url.strip()
    lowered = target.lower()
    if lowered in _KNOWN_SITES:
        url = _KNOWN_SITES[lowered]
    elif target.startswith("http://") or target.startswith("https://"):
        url = target
    else:
        url = f"https://{target}" if "." in target else f"https://{target}.com"
    # NOTE: deliberately no webbrowser.open() here. This tool runs wherever the
    # SERVER is hosted — on a remote deployment that's not the user's device at all,
    # so opening a browser tab server-side would do nothing anyone can see. Instead
    # this just resolves the URL; server.py relays it to the browser that's actually
    # connected, which opens (or offers) it there.
    return {"result": "ok", "url": url}


async def run_shell_command(command: str) -> dict:
    """Run a shell command on the user's computer and return its output.

    For quick, everyday things: listing a directory, checking a process, running a small
    script. Not for long-running or interactive programs. Refuses destructive-looking
    commands (deleting things, formatting, shutdown/reboot, password changes).

    Args:
        command: the shell command to run.
    """
    if _looks_dangerous(command):
        return {"result": "refused", "reason": "that command looks destructive or irreversible — not running it."}
    try:
        proc = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        except asyncio.TimeoutError:
            proc.kill()
            return {"result": "error", "message": "command timed out after 15s"}
        return {
            "result": "ok",
            "stdout": (stdout or b"").decode(errors="ignore")[-2000:],
            "stderr": (stderr or b"").decode(errors="ignore")[-500:],
            "returncode": proc.returncode,
        }
    except Exception as e:
        return {"result": "error", "message": str(e)}


async def search_files(query: str, root: str = "~") -> dict:
    """Search for files by name under a directory.

    Args:
        query: filename or partial filename to search for (e.g. "invoice" or "*.pdf").
        root: directory to search under. Defaults to the user's home directory.
    """
    base = Path(root).expanduser()
    if not base.exists():
        return {"result": "error", "message": f"{base} does not exist"}
    pattern = query if any(c in query for c in "*?[]") else f"*{query}*"
    matches = []
    try:
        for path in base.rglob(pattern):
            matches.append(str(path))
            if len(matches) >= 20:
                break
        return {"result": "ok", "matches": matches, "count": len(matches)}
    except Exception as e:
        return {"result": "error", "message": str(e)}


async def get_system_status() -> dict:
    """Report basic system status: OS, machine type, and free disk space."""
    info = {"os": platform.system(), "os_version": platform.release(), "machine": platform.machine()}
    try:
        total, used, free = shutil.disk_usage(str(HOME))
        info["disk_free_gb"] = round(free / (1024**3), 1)
        info["disk_total_gb"] = round(total / (1024**3), 1)
    except Exception:
        pass
    return {"result": "ok", **info}


async def web_search(query: str) -> dict:
    """Search the live web for current information and summarize the answer.

    Use for anything you can't answer from what you already know: current events,
    prices, hours, recent facts.

    Args:
        query: what to look up.
    """
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=os.getenv("SEARCH_MODEL", "gemini-flash-latest"),
            contents=query,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            ),
        )
        return {"result": "ok", "answer": response.text}
    except Exception as e:
        return {"result": "error", "message": str(e)}
