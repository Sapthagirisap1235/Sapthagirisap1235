FROM python:3.12-slim

WORKDIR /app

# System deps: none beyond what's needed to build sqlalchemy/uvicorn wheels.
RUN pip install --no-cache-dir --upgrade pip uv

COPY pyproject.toml ./
COPY adk ./adk
COPY frontend_jarvis ./frontend_jarvis
COPY assets ./assets

RUN uv pip install --system --no-cache .

ENV PYTHONUNBUFFERED=1
# The sqlite memory DB lives here by default (DB_URL in .env) — mount a volume
# at /data and point DB_URL at sqlite:////data/jarvis_memory.db to persist it
# across container rebuilds, not just restarts.
RUN mkdir -p /data
WORKDIR /app

EXPOSE 8000
# Shell form (not exec-array form) so $PORT actually gets expanded — Render
# (and most PaaS hosts) assign the port dynamically via this env var rather
# than letting you hardcode one. Falls back to 8000 for `docker run` locally.
CMD uvicorn adk.server:app --host 0.0.0.0 --port ${PORT:-8000}
