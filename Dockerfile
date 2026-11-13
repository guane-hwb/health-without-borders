# Base image pinned by digest, so a rebuild of the same commit gets the same
# operating system packages. To take security updates, replace the digest
# with the one `docker pull python:3.11-slim` reports (docs/infrastructure/
# gcp-deploy.md § 7). This one was published 2026-11-05.
ARG PYTHON_IMAGE=python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535

# ---- build stage: resolve the virtualenv from the lockfile -----------------
FROM ${PYTHON_IMAGE} AS builder

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
FROM ${PYTHON_IMAGE}

WORKDIR /app

# Run as an unprivileged user instead of root.
RUN useradd --create-home --uid 10001 appuser

# Owned by root: the process can read and run the code and the virtualenv but
# not change them, so a flaw that lets it write files cannot plant code that
# the next import would run. The app writes nothing under /app.
COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH"
# Bytecode is compiled at build time (UV_COMPILE_BYTECODE); do not try to
# write __pycache__ into a directory the process cannot write to.
ENV PYTHONDONTWRITEBYTECODE=1

USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
