# syntax=docker/dockerfile:1.7-labs
#
# Track B — multi-stage production Dockerfile.
#
# Mirrors the structure of track-a-jd-native/Dockerfile but for the
# Python / Poetry / Dagster stack.
#
# Stages:
#   deps     — Poetry install (no dev) cached on lockfile
#   build    — sanity-compile (no real compile for Python; placeholder
#              for any code-gen step a real production build might add)
#   runtime  — slim image with venv copied across; runs `dagster api grpc`
#              by default so the container can be picked up by a Dagster
#              webserver or daemon outside.

ARG PYTHON_VERSION=3.12-slim

# ---------------------------------------------------------------------------
# Stage 1: dependency installation
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION} AS deps
WORKDIR /app

ENV POETRY_VERSION=1.8.3 \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install --no-install-recommends -y curl build-essential \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir "poetry==${POETRY_VERSION}"

COPY pyproject.toml poetry.lock* ./
RUN poetry install --no-root --only main

# ---------------------------------------------------------------------------
# Stage 2: build
# ---------------------------------------------------------------------------
FROM deps AS build
WORKDIR /app

COPY dagster_project ./dagster_project
COPY parser ./parser
COPY dbt ./dbt
COPY streaming ./streaming

# Validate that the Dagster Definitions module imports cleanly.
RUN poetry run python -c "from dagster_project.definitions import defs; print('defs OK')"

# ---------------------------------------------------------------------------
# Stage 3: runtime
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION} AS runtime
WORKDIR /app

# Drop privileges.
RUN useradd --create-home --uid 1000 dagster
USER dagster

ENV VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --chown=dagster:dagster --from=build /app /app

EXPOSE 4000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import dagster_project.definitions" || exit 1

# Default: start a Dagster gRPC server. The webserver and daemon run in
# separate containers wired through docker-compose.yml.
CMD ["dagster", "api", "grpc", "--host", "0.0.0.0", "--port", "4000", \
     "--module-name", "dagster_project.definitions"]
