# ---- build stage: resolve the virtualenv from the lockfile -----------------
FROM python:3.11-slim AS builder

# Pinned by version and digest so a rebuild of the same commit uses the same
# uv (the version that produced uv.lock).
COPY --from=ghcr.io/astral-sh/uv:0.6.8@sha256:cb641b1979723dc5ab87d61f079000009edc107d30ae7cbb6e7419fdac044e9f /uv /bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./

# Production dependencies only: no dev tools, no documentation tooling. Every
# dependency ships a wheel (psycopg2-binary bundles libpq), so no compiler.
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

RUN uv sync --frozen --no-dev

# ---- runtime stage: the app and its virtualenv, nothing else --------------
FROM python:3.11-slim

WORKDIR /app

# Run as an unprivileged user instead of root.
RUN useradd --create-home --uid 10001 appuser

COPY --from=builder --chown=appuser:appuser /app /app

ENV PATH="/app/.venv/bin:$PATH"

USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
