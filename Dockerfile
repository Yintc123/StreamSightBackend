# syntax=docker/dockerfile:1.7
#
# Multi-stage build for StreamSight Backend (FastAPI + uv).
#
#   deps     — uv sync (production deps only, frozen lockfile)
#   runtime  — slim image; copies venv + app source; runs uvicorn

# === Stage 1: deps ===
FROM python:3.13-slim AS deps
WORKDIR /app

# asyncmy has a Cython extension that requires gcc to compile
RUN apt-get update && apt-get install -y --no-install-recommends \
      gcc build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# === Stage 2: runtime ===
FROM python:3.13-slim AS runtime
WORKDIR /app

# Non-root user
RUN addgroup --system --gid 1001 appgroup && \
    adduser --system --uid 1001 --ingroup appgroup appuser

COPY --from=deps /app/.venv /app/.venv
COPY --chown=appuser:appgroup . .

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER appuser

EXPOSE 8000

# Run migrations then start the server.
# alembic upgrade head is idempotent — safe to run on every boot.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
