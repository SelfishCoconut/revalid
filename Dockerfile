# syntax=docker/dockerfile:1
#
# revalid — the deployable application image (ADR-0044).
#
# Two stages: node builds the React SPA (FR-11), python runs the FastAPI backend
# and serves that build at "/". The retest sandbox is deliberately NOT part of
# this image — it is a separate toolbox image (`make sandbox-image`) that the
# app launches as a *sibling* container through the host Docker socket, so the
# egress lock stays a property of the session network (ADR-0025/0041) rather
# than of anything baked in here.

# ---- 1. Build the SPA (FR-11) ---------------------------------------------
# Node 22 matches the version the frontend CI job gates on.
FROM node:22-slim AS ui
WORKDIR /ui
# Manifests first so `npm ci` re-runs only when dependencies actually change,
# not on every source edit.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 2. Application runtime -----------------------------------------------
FROM python:3.12-slim

# uv resolves and installs the locked dependency set, exactly as CI does.
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

# curl backs the HEALTHCHECK below; nothing else at runtime shells out.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Metadata and lockfile before the sources: the dependency layer then caches
# independently of application edits. README/LICENSE are required because
# `uv_build` reads them from `[project]`.
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/

# --locked   : install exactly what CI tested; fail rather than re-resolve.
# --extra sandbox : the Docker SDK the agentic retest needs (ADR-0025).
# --no-dev   : no test, lint or documentation tooling in a runtime image.
RUN uv sync --locked --no-dev --extra sandbox

# The app resolves the SPA from its own package location (`parents[2]/frontend
# /dist`), so the build has to land beside src/, not in the working directory.
COPY --from=ui /ui/dist ./frontend/dist

ENV PATH="/app/.venv/bin:$PATH"

# Fail the build — loudly, here — rather than serve a blank page at runtime.
# `_SPA_DIST` is derived from the installed package's path, so a change in how
# the project is installed (editable vs copied) would silently move it.
RUN python -c "from revalid.app import _SPA_DIST; \
assert (_SPA_DIST / 'index.html').is_file(), f'SPA build is not where the app looks: {_SPA_DIST}'"

# `create_app` opens ./revalid.db relative to the process working directory, so
# the working directory *is* where operator state lives. Pointing it at the
# mounted volume keeps the database across a rebuild without the application
# needing to know it is containerised. The code stays under /app.
WORKDIR /data
VOLUME ["/data"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# 0.0.0.0 binds only inside the container's own network namespace; the compose
# file publishes the port to 127.0.0.1 alone, so nothing is reachable off the
# host (NFR-03).
CMD ["uvicorn", "--factory", "revalid.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
