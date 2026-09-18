# JARVIS mode — a live voice assistant that acts on your computer

This is live-dj's ADK build (`adk/`), repointed at a JARVIS-style persona with real
tools instead of just music control. It's additive — nothing about the original
Mira demo (`genai_sdk/`, `frontend/`) was touched, so both still work.

## What's new

| File | What it does |
|---|---|
| `adk/jarvis_persona.py` + `assets/jarvis_persona.txt` | JARVIS's character + system instruction (replaces Mira's, for this build only) |
| `adk/jarvis_tools.py` | Five new tools: `open_app`, `run_shell_command`, `search_files`, `get_system_status`, `web_search` |
| `adk/agent.py` | Now loads the JARVIS persona + all tools (JARVIS's + the original music ones) |
| `adk/server.py` | Serves `frontend_jarvis/` instead of `frontend/`; broadcasts a `tool_call` event so you can see in the transcript when JARVIS actually runs something, not just when it talks |
| `frontend_jarvis/` | A copy of the original client, relabeled, with a line in the transcript for each tool call |

## What JARVIS can do

- **Talk, live** — same barge-in / interruption as the original: talk over it and it stops instantly.
- **`open_app(app_name)`** — launches a program *installed on the machine* (`open -a` on macOS, `xdg-open` on Linux, `start` on Windows). Not for websites — see `open_website`.
- **`open_website(site_or_url)`** — opens a website or web app in the default browser (YouTube, Gmail, Google Maps, GitHub, Netflix, or any URL). A handful of common names are pre-resolved to the right URL; anything else is opened as `https://<name>.com` or as-is if it's already a full URL.
- **`run_shell_command(command)`** — runs a shell command locally and reads back stdout/stderr. Times out after 15s. Refuses anything that matches a destructive-command blocklist (`rm -rf`, `shutdown`, `passwd`, `sudo`, disk formatting, etc.) — this is a safety net, not a sandbox, so only run this against a machine you trust the assistant on.
- **`search_files(query, root)`** — finds files by name under a directory (defaults to your home folder).
- **`get_system_status()`** — OS, machine type, free disk space.
- **`web_search(query)`** — looks something up on the live web. Implemented as a *separate*, ordinary (non-live) Gemini call with Google Search grounding, because the Live API doesn't allow mixing its built-in `google_search` tool with custom function tools in the same session — this sidesteps that by making the grounded call *inside* one function tool instead.
- **`play_playlist` / `play_track` / `skip` / `pause`** — the original music tools, still there if you want ambient music too.

## Run it

Same quick start as the main README, just point at the ADK server:

```bash
uv sync
cp .env.example .env      # paste your GOOGLE_API_KEY, JARVIS_USERNAME/PASSWORD, SESSION_SECRET
uv run uvicorn adk.server:app --port 8000
```

Set `COOKIE_SECURE=false` in `.env` for this local `http://localhost` run — leave it `true`
for any real deployment (see below). Open <http://localhost:8000>, log in with the username/
password you set, tap 🎙, and try:
- *"open Visual Studio Code"*
- *"what's my free disk space?"*
- *"search my home folder for anything with 'invoice' in the name"*
- *"what's the weather in Bengaluru right now"* (routes through `web_search`)
- *"list the files in my downloads folder"* (routes through `run_shell_command`)

Talk over it mid-sentence to interrupt, same as the original demo.

## Persistent memory (added)

There's now a single continuous conversation, not a fresh session every time you connect.
`adk/server.py` uses ADK's `DatabaseSessionService` (sqlite by default, via `DB_URL` in
`.env`) instead of the original `InMemorySessionService`, and every websocket connection
resumes the *same* session id (`MEMORY_USER_ID` / `MEMORY_SESSION_ID` in `server.py`) rather
than creating a new one. That means:

- Reconnecting, refreshing the page, or restarting the server does **not** erase what
  you've talked about — ADK reloads the full prior conversation from the database as context.
- It's genuinely single-user by design: one continuous history for one owner, not separate
  threads per person. If you want multiple people with separate memories, you'd key the
  session id off a logged-in identity instead of the fixed `"main"` constant.
- Over a long enough time, that history keeps growing and eventually competes with the
  model's context window (and costs more per turn, since Live resends context each turn).
  There's no automatic summarization/trimming here — if that becomes a problem, look at
  ADK's session event compaction features, or periodically start a fresh `MEMORY_SESSION_ID`.

## Login + public deployment (added)

The server now requires a login (`adk/auth.py`) — a single username/password from `.env`,
a signed session cookie, no external auth service. Every HTTP route except `/login` is
gated by `AuthMiddleware`, and the websocket handler checks the same cookie before accepting
a connection. **The server refuses to start serving pages at all if `JARVIS_PASSWORD` /
`SESSION_SECRET` aren't set** — it fails closed rather than silently running open.

To actually put this on the public internet with HTTPS and a real domain, see
**[`DEPLOY.md`](./DEPLOY.md)** — it walks through a Docker + Caddy setup (Caddy handles
automatic Let's Encrypt certificates) on any VPS. Once deployed, the same page works from
your phone's browser too — no separate mobile app, just the same URL over HTTPS (mic access
requires HTTPS on mobile, which the deployment gives you).

**Read the security section in `DEPLOY.md` before deploying** — exposing `run_shell_command`
to the internet behind a single password is a meaningfully different risk than running it
on your own laptop.

## Safety notes — read before giving it a shell

- `run_shell_command` executes on **your real machine** with **your user's permissions**. The blocklist in `jarvis_tools.py` catches the obvious destructive patterns, but it is not a sandbox and a determined or confused model could still do something you didn't want.
- For anything beyond "look things up and run harmless local commands," consider:
  - Running it as a low-privilege user, or inside a container/VM.
  - Tightening `_BLOCKED_SUBSTRINGS` in `jarvis_tools.py`, or switching to an allowlist of specific commands instead of a blocklist.
  - Adding a confirmation step in the frontend (the new `tool_call` event already gives you a hook to add a "did you mean to run this?" prompt before it's spoken back as done).
- Nothing here is exposed to the internet — it's a local FastAPI server on `localhost`. Don't deploy this as-is to a public URL without adding authentication.

## Known limitations

- **Self-listening feedback loop ("it stops replying" / garbled repeated transcripts)**: without headphones, JARVIS's own voice leaks from your speakers back into the mic and gets sent up as if you'd said it — the session ends up "talking" to its own echo, which is what garbled, repeating lines like "Cheta Raja sir. Cheta Raja sir." are. Two layers of mitigation now, both in `frontend_jarvis/main.js`: (1) the mic doesn't forward audio upstream while JARVIS is speaking unless it's loud enough to be a genuine interruption, and (2) that "loud enough" check now also requires several consecutive loud frames (not one spike) and ignores the first ~450ms after speech starts, where a stray echo tail is most likely. Headphones are still the more bulletproof fix if your hardware's echo cancellation is weak.
- **"Open X" for a website vs. an app**: `open_app` only knows about programs actually installed on the machine — "open YouTube" will fail through it, since Windows/macOS/Linux have no such installed program. JARVIS is instructed to route anything web-based through `open_website` instead, and anything YouTube-specific through `youtube_search` (see below). If it still guesses wrong for something not in `_KNOWN_SITES` (in `jarvis_tools.py`), just say "that's a website" and it'll retry with the right tool — or add the site to that dict yourself.
- **Speaking pace**: the Gemini Live API's `SpeechConfig` only exposes a *voice name*, not a numeric speaking-rate or prosody control — [this is a known, currently-open gap](https://github.com/google/adk-docs/issues/487) in the API, not something this project can fully fix client-side. Two things help:
  - The persona instruction (`adk/jarvis_persona.py`) explicitly asks for an unhurried, one-sentence-at-a-time delivery — native-audio models do respond to this kind of natural-language pacing cue, though not as reliably as an actual parameter would.
  - Try a different `LIVE_VOICE` in `.env` — some prebuilt voices are simply calmer/slower than others. Options: `Puck, Charon, Kore, Fenrir, Aoede, Leda, Orus, Zephyr` (plus a longer list of newer ones — `Achernar`, `Enceladus`, `Umbriel`, `Vindemiatrix`, etc. — that may not all be available on every model yet). `Kore`, `Umbriel`, and `Vindemiatrix` tend to read as calmer/more measured than `Puck` or `Fenrir`.
  - As a last resort you could slow down `AudioBufferSourceNode.playbackRate` in `frontend_jarvis/main.js`'s `playVoice()` (e.g. `src.playbackRate.value = 0.9`) — but this also lowers the pitch slightly, since it isn't true time-stretching.
- **Speech cutting off mid-sentence on a remote deployment**: if this happens even with the anti-echo hardening above, the likely cause is different — Render's free tier (and some proxies generally) can drop a websocket after a stretch with no traffic, truncating whatever was mid-stream. `adk/server.py` now sends a small keepalive ping every 20s and the client acks it (`main.js`'s `"ping"` handler), which should prevent this; if it's still happening, it's worth trying a paid Render instance (free tier's cold-start/spin-down behavior is a separate, known source of interruptions) or checking `docker compose logs` / Render's logs for a disconnect right when it happens.

## Running on a remote server (Render, a VPS, etc.) — LOCAL_MODE

`open_app`, `run_shell_command`, `search_files`, and `get_system_status` act on whatever
machine the SERVER runs on. Locally, that's your own computer — fine. On a remote
deployment, that's the remote box, not your phone or laptop — asking it to "open Chrome"
there does nothing you can see, which is the actual explanation if you've deployed this and
those tools "aren't working."

Set `LOCAL_MODE=false` in `.env` for any remote deployment (`RENDER.md` and `DEPLOY.md` both
set this). `adk/agent.py` then drops those four tools entirely, and JARVIS's own instructions
(`adk/jarvis_persona.py`'s `build_instruction()`) are rewritten to match — it's told plainly
that it's running remotely and won't claim to control a device it can't reach. What still
works remotely: `open_website` (opens a link in *your* browser, not the server's — see below),
`youtube_search`/`youtube_play`/`scroll_youtube_results`, `web_search`, and the music tools.

## YouTube — search + an embedded, voice-controlled player

Rather than trying to open a real youtube.com tab from a server with no browser (and
youtube.com has no supported way to be remote-controlled from outside anyway), JARVIS gets
its own small YouTube surface, built into the page:

- **`youtube_search(query)`** calls the real YouTube Data API v3 server-side and pushes the
  results into a scrollable list right on the JARVIS page (`#yt-panel` in
  `frontend_jarvis/index.html`) — thumbnail, title, channel.
- **`youtube_play(index_or_video_id)`** embeds one of those results (e.g. "play the second
  one") in a 16:9 player on the same page.
- **`scroll_youtube_results(direction)`** scrolls that results list up or down on request.
- Clicking a result in the list plays it directly too, no voice needed.

**Requires `YOUTUBE_API_KEY`** in `.env` — get one from
[Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → enable
"YouTube Data API v3" → Credentials → Create API key. Without it, `youtube_search` tells
JARVIS plainly that it can't search, rather than failing silently or hallucinating results.

`open_website` no longer opens anything server-side — it resolves the URL and hands it to
*your* browser (`frontend_jarvis/main.js`'s `openUrl()`), which tries `window.open()` and
falls back to a tappable link in the transcript if the popup gets blocked (likely, since this
isn't triggered by a direct click). This was also broken the same way `open_app` was on a
remote deployment — worth knowing even if you're running locally, since it's a real fix either way.

## `DEPLOY.md` vs `RENDER.md`

Two deployment paths, same app: `DEPLOY.md` is a raw VPS + Docker + Caddy (you manage
everything); `RENDER.md` is Render specifically (simpler, handles HTTPS for you, but read its
persistent-memory section — Render's filesystem is ephemeral, so the default sqlite setup
won't survive a redeploy there without either a Render Postgres or a paid persistent disk).

`frontend_jarvis/index.html`'s orb is a Siri-style swirling conic gradient (blue → purple → pink → orange) instead of the original flat blue disc — it rotates continuously and speeds up/slows down with state (idle drifts slowly, listening picks up, speaking swirls fastest and brightens). Uses `conic-gradient`, which needs a reasonably current browser (Chrome/Edge/Firefox/Safari all support it) — if you want to retheme the palette, the five color stops are in the `.orb { background: conic-gradient(...) }` rule near the top of the `<style>` block.


