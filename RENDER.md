# Deploying JARVIS on Render

Render is simpler than the raw-VPS path in `DEPLOY.md` — no Caddy, no manual
Docker Compose, HTTPS and a public URL come for free. Two things Render does
differently that the code already accounts for:

- Render assigns your port dynamically via a `$PORT` env var — `Dockerfile`'s
  `CMD` already reads that (falls back to 8000 if unset, for local `docker run`).
- Render's health checker hits your app **without** a login cookie — `/healthz`
  is deliberately excluded from the auth gate so that check doesn't fail.

**Read the security note in `DEPLOY.md` before doing this** — it applies here too.

## Steps

1. **Push this project to a GitHub repo** (Render deploys from a git repo, not
   a zip upload). Create a new repo and push `live-dj-main`'s contents to it.

2. **In the Render dashboard**: New → Web Service → connect that repo.

3. **Runtime**: Render should auto-detect the `Dockerfile` and offer "Docker"
   as the environment — pick that (not "Python"), so you don't need to
   redeclare the build/start commands by hand.

4. **Instance type**: the free tier works for trying it out, but see the
   memory note below — free instances spin down when idle and lose anything
   not in a database.

5. **Environment variables** — in the service's "Environment" tab, add:
   ```
   GOOGLE_GENAI_USE_VERTEXAI=FALSE
   GOOGLE_API_KEY=<your real key>
   LIVE_MODEL=gemini-3.1-flash-live-preview
   LIVE_VOICE=Charon
   SEARCH_MODEL=gemini-flash-latest
   OTEL_SDK_DISABLED=true
   JARVIS_USERNAME=<pick one>
   JARVIS_PASSWORD=<pick a strong one>
   SESSION_SECRET=<generate: python3 -c "import secrets; print(secrets.token_hex(32))">
   COOKIE_SECURE=true
   DB_URL=<see the memory section below>
   LOCAL_MODE=false
   YOUTUBE_API_KEY=<optional — see the YouTube section below>
   ```
   **`LOCAL_MODE=false` matters here** — it drops `open_app`, `run_shell_command`,
   `search_files`, and `get_system_status` from JARVIS's tools entirely. Those act on
   whatever machine the server runs on, which on Render is Render's container, not your
   phone or laptop — leaving `LOCAL_MODE` at its local-dev default would let JARVIS think
   it can open apps or read files on a device it can't actually reach.
   Don't upload `.env` itself — Render's env var UI *is* your `.env` here.

6. **Health check path**: in the service's "Settings" → "Health Check Path",
   set it to `/healthz`. Without this, Render's default check hits `/`, gets
   redirected to `/login`, and may mark the deploy unhealthy.

7. **Deploy.** Render builds the Docker image and gives you a URL like
   `https://jarvis-xxxx.onrender.com` with HTTPS already on — usable from your
   phone immediately, no extra setup. Add a custom domain later from the
   service's "Settings" → "Custom Domains" if you want one; Render issues the
   certificate for it automatically too.

## Persistent memory on Render — read this before you rely on it

Render's web services have an **ephemeral filesystem** — anything written to
disk (including a sqlite file) disappears on every redeploy, and on a free
instance, also whenever it spins down from inactivity and back up. That
directly undermines the "remembers everything" feature we built. Two ways to
actually fix it:

**Option A — Render Postgres (recommended).** Create a Postgres database from
the Render dashboard (New → PostgreSQL; a free tier exists but expires after
some time — check current terms on Render's pricing page since this changes).
Take the "Internal Database URL" it gives you, and set:
```
DB_URL=postgresql+asyncpg://<the rest of the connection string Render gave you>
```
(swap `postgresql://` for `postgresql+asyncpg://` — ADK's `DatabaseSessionService`
needs the async driver name; `asyncpg` is already in `pyproject.toml`). This
survives redeploys and spin-downs, since the database is a separate service.

**Option B — a Render Disk.** Attach a persistent disk to the web service
(Settings → Disks; this requires a paid instance type, not the free tier),
mount it at e.g. `/data`, and set:
```
DB_URL=sqlite+aiosqlite:////data/jarvis_memory.db
```
Simpler to set up than Postgres, but costs more than the free Postgres option
and only works on paid instance types.

If you skip both and leave the default sqlite path, it'll work fine right up
until the first redeploy or spin-down — then memory resets. Pick one of the
two above for anything you actually want to rely on.

## Everything else

Auth, the shell-command safety notes, and the general "read this before
exposing a shell to the internet" section are the same as the VPS path — see
`DEPLOY.md`'s security section, it isn't Render-specific.

## Speech cutting off mid-sentence?

Render's free tier is known to drop idle websockets after a few minutes, which would
truncate whatever JARVIS was saying mid-stream. `adk/server.py` already sends a small
keepalive ping every 20 seconds specifically to prevent this (the client acks it, and that
ack counts as inbound traffic keeping the connection "active" from Render's point of view).
If it still happens, it's worth trying a paid instance — free-tier spin-down/cold-start
behavior is a separate, known source of interruptions beyond just the idle-websocket issue.

## YouTube search + player

`youtube_search` needs its own `YOUTUBE_API_KEY` (separate from `GOOGLE_API_KEY`) — get one
from [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → enable
"YouTube Data API v3" → Credentials → Create API key, then add it to the environment
variables above. Without it, JARVIS will say plainly that it can't search YouTube rather
than failing silently.
