# Deploying JARVIS publicly

This gets you a real `https://your-domain.com` URL, reachable from your phone or
anywhere else, with a login gate and memory that survives restarts.

**Read the security note at the bottom before you do this.** This build can run
shell commands on the machine it's deployed to — that's a very different risk
profile once it's reachable from the internet instead of just your laptop.

## What you need

- A domain name (or a subdomain) you can point at a server — any registrar works.
- A small cloud VPS with Docker available. Any provider is fine (DigitalOcean,
  Hetzner, Linode, a spare machine with a public IP, etc.) — the cheapest tier
  is plenty for a single-user voice assistant.
- Your `GOOGLE_API_KEY` from Google AI Studio.

## Steps

1. **Point your domain at the server.** In your registrar's DNS settings, add
   an `A` record for the domain (or subdomain, e.g. `jarvis.yourdomain.com`)
   pointing at your VPS's public IPv4 address. This can take a few minutes to
   propagate.

2. **Get the code onto the server and install Docker**, if it isn't already:
   ```bash
   curl -fsSL https://get.docker.com | sh
   ```

3. **Copy the project to the server** (scp, git clone, whatever you normally
   use), then `cd` into `live-dj-main`.

4. **Fill in `.env`** — copy `.env.example` to `.env` and set:
   - `GOOGLE_API_KEY` — your real key
   - `JARVIS_USERNAME` / `JARVIS_PASSWORD` — pick your own login
   - `SESSION_SECRET` — generate one: `python3 -c "import secrets; print(secrets.token_hex(32))"`
   - Leave `COOKIE_SECURE=true` (you're serving over HTTPS now)
   - `DB_URL` — leave as-is; `docker-compose.yml` overrides it to the persistent volume path

5. **Put your real domain in `Caddyfile`** — replace `your-domain-here.com` with
   the domain from step 1.

6. **Start it:**
   ```bash
   docker compose up -d --build
   ```
   Caddy will automatically request and renew a Let's Encrypt certificate for
   your domain the first time it starts — no manual certbot steps.

7. **Open `https://your-domain.com`** from any browser, on any device
   (including your phone — mic access requires HTTPS, which you now have),
   log in with the username/password from step 4, and talk to it.

## Checking it's actually working

```bash
docker compose logs -f jarvis      # app logs
docker compose logs -f caddy       # certificate issuance / proxy logs
```

## Updating later

```bash
git pull   # or however you get new code onto the server
docker compose up -d --build
```
The memory database lives in the `jarvis-data` Docker volume, not in the
container, so rebuilding doesn't erase it.

## Security note — please actually read this

Exposing `run_shell_command` to the public internet behind nothing but a
password is a meaningfully bigger risk than running it on your own laptop:

- **Use a strong, unique `JARVIS_PASSWORD`.** This is the only thing standing
  between the internet and a shell on your server.
- **Consider disabling `run_shell_command` entirely for a public deployment.**
  Open `adk/agent.py` and remove it from the `tools=[...]` list (and the
  corresponding line in `adk/jarvis_persona.py`'s tool list) if you don't
  actually need remote shell access day-to-day — `open_website`, `web_search`,
  `search_files`, and `get_system_status` carry far less risk.
- **The blocklist in `jarvis_tools.py` is a safety net, not a sandbox.** It
  catches obvious destructive patterns, not a determined attacker who gets
  your password.
- **This Docker setup already helps**: the app runs inside a container, so a
  compromised session gets shell access *inside that container*, not directly
  on your host machine — but the container still has network access and can
  still do damage inside itself (or reach anything else on the same Docker
  network). Don't treat the container as a full sandbox either.
- **Session cookies last 30 days** (`SESSION_TTL_SECONDS` in `adk/auth.py`).
  Shorten that if you want to log out more often, or hit `/logout` manually.
