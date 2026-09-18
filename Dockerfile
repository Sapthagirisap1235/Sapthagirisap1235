FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip uv

# Install DEPENDENCIES ONLY first (better Docker layer caching — this layer only
# rebuilds when pyproject.toml/uv.lock change, not on every source edit).
# IMPORTANT: this is `uv sync`, not `uv pip install .`. pyproject.toml already
# has [tool.uv] package = false (this is an app, not a library to publish) —
# but that setting is only honored by `uv sync`. `uv pip install .` ignores it
# and tries to build/install "." as an installable package regardless, which
# fails: setuptools' automatic package discovery can't resolve a flat layout
# with adk/, frontend_jarvis/, and assets/ all sitting at the top level
# ("Multiple top-level packages discovered" / "explicitly set packages").
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# NOW copy the source — changes here don't invalidate the dependency layer above.
COPY adk ./adk
COPY frontend_jarvis ./frontend_jarvis
COPY assets ./assets

ENV PYTHONUNBUFFERED=1
# The sqlite memory DB lives here by default (DB_URL in .env) — mount a volume
# at /data and point DB_URL at sqlite:////data/jarvis_memory.db to persist it
# across container rebuilds, not just restarts.
RUN mkdir -p /data

EXPOSE 8000
# Shell form (not exec-array form) so $PORT actually gets expanded — Render
# (and most PaaS hosts) assign the port dynamically via this env var rather
# than letting you hardcode one. Falls back to 8000 for `docker run` locally.
# `uv run` picks up the venv `uv sync` created above automatically.
CMD uv run uvicorn adk.server:app --host 0.0.0.0 --port ${PORT:-8000}
