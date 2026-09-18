"""Single-user cookie auth for jarvis-adk.

Deliberately minimal — one username/password pair from the environment, a
signed cookie, no database, no third-party auth library. This is sized for
"just me, one login for my own JARVIS" exposed on the public internet, not
for multiple accounts. If you need real multi-user accounts later, replace
this module; nothing else in server.py depends on its internals, only on
`is_authenticated(request_or_websocket)` and the /login, /logout routes.
"""
import hmac
import hashlib
import os
import time

COOKIE_NAME = "jarvis_session"
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days

JARVIS_USERNAME = os.getenv("JARVIS_USERNAME", "owner")
JARVIS_PASSWORD = os.getenv("JARVIS_PASSWORD")  # required — see .env.example
SESSION_SECRET = os.getenv("SESSION_SECRET")     # required — see .env.example

# Only send the cookie over HTTPS. Set COOKIE_SECURE=false in .env for local
# http://localhost testing; leave it true (the default) for any real deployment.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() != "false"


def configured() -> bool:
    """True once JARVIS_PASSWORD and SESSION_SECRET are actually set."""
    return bool(JARVIS_PASSWORD) and bool(SESSION_SECRET)


def make_session_cookie(username: str) -> str:
    expiry = int(time.time()) + SESSION_TTL_SECONDS
    payload = f"{username}:{expiry}"
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_session_cookie(token: str | None) -> bool:
    if not token or not SESSION_SECRET:
        return False
    try:
        username, expiry, sig = token.split(":", 2)
        payload = f"{username}:{expiry}"
        expected = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        return int(expiry) > time.time()
    except Exception:
        return False


def check_credentials(username: str, password: str) -> bool:
    if not configured():
        return False
    return username == JARVIS_USERNAME and hmac.compare_digest(password, JARVIS_PASSWORD)


LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>jarvis · sign in</title>
<style>
  body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
    background:#0a0d10; color:#dce8ee; font-family: ui-sans-serif, system-ui, Inter, sans-serif; }}
  form {{ display:flex; flex-direction:column; gap:14px; width:260px; }}
  h1 {{ font-size:12px; letter-spacing:0.3em; text-transform:uppercase; color:rgba(220,232,238,0.5);
    text-align:center; margin:0 0 10px; }}
  input {{ background:rgba(255,255,255,0.04); border:1px solid rgba(220,232,238,0.25); border-radius:8px;
    padding:10px 12px; color:#dce8ee; font-size:14px; }}
  input:focus {{ outline:none; border-color:#7ec8e3; }}
  button {{ background:transparent; color:#dce8ee; border:1px solid rgba(220,232,238,0.35);
    border-radius:999px; padding:10px; font-size:11px; letter-spacing:0.2em; text-transform:uppercase;
    cursor:pointer; }}
  button:hover {{ border-color:#7ec8e3; }}
  .err {{ color:#ff8a8a; font-size:12px; text-align:center; margin:0; }}
</style></head>
<body>
  <form method="post" action="/login">
    <h1>jarvis</h1>
    {error}
    <input name="username" placeholder="username" autocomplete="username" required />
    <input name="password" type="password" placeholder="password" autocomplete="current-password" required />
    <button type="submit">sign in</button>
  </form>
</body></html>
"""
