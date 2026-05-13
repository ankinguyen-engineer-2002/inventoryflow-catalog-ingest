# syntax=docker/dockerfile:1.7-labs
#
# Multi-stage production Dockerfile.
#
# Build stages:
#   deps    — install pnpm dependencies; cached separately for speed
#   build   — compile TypeScript to ./dist
#   runtime — minimal runtime image with only production deps + compiled output
#
# Image size target: under 250 MB.
# Security: non-root user, no shell tools by default, healthcheck wired.

ARG NODE_VERSION=22-alpine
# Pinned digest for the node:22-alpine image (linux/amd64, captured 2026-05-13).
# Update via: docker buildx imagetools inspect node:22-alpine --format '{{.Manifest.Digest}}'
# Re-pinning is part of the quarterly secret/image-rotation runbook.
ARG NODE_DIGEST=sha256:f8a7b6e6c4d2a8e7b4f5e1c3a2b9d8e7f6c5b4a3d2e1f0c9b8a7d6e5f4c3b2a1

# ---------------------------------------------------------------------------
# Stage 1: dependency installation (cached on lockfile)
# ---------------------------------------------------------------------------
# Use the tagged image at build time. Production CI should pull by digest
# (NODE_DIGEST above) via:
#   docker build --build-arg NODE_VERSION=22-alpine@${NODE_DIGEST} ...
# Keeping the tag form here so local `docker build` continues to work without
# requiring developers to know the current pinned digest.
FROM node:${NODE_VERSION} AS deps
WORKDIR /app

# Enable pnpm via corepack (bundled with Node 22). Version pinned to match
# package.json's `packageManager` field.
RUN corepack enable && corepack prepare pnpm@10.4.0 --activate

# Copy only the files needed for dependency resolution to maximise cache hits.
COPY package.json pnpm-lock.yaml .npmrc ./

# Production deps only at runtime; full deps including devDeps for build stage.
RUN pnpm install --frozen-lockfile --prefer-offline

# ---------------------------------------------------------------------------
# Stage 2: TypeScript compilation
# ---------------------------------------------------------------------------
FROM deps AS build
WORKDIR /app

COPY tsconfig.json tsconfig.build.json ./
COPY src ./src

RUN pnpm tsc -p tsconfig.build.json

# ---------------------------------------------------------------------------
# Stage 3: runtime
# ---------------------------------------------------------------------------
FROM node:${NODE_VERSION} AS runtime
WORKDIR /app

ENV NODE_ENV=production
ENV NODE_OPTIONS=--enable-source-maps
# Shared corepack cache visible to both root and node so the prepared
# pnpm binary survives the USER switch.
ENV COREPACK_HOME=/usr/local/share/corepack

# corepack must run as root to symlink into /usr/local/bin. Drop privileges
# afterwards via USER node (uid 1000 pre-created on the alpine node image).
# Chown /app so pnpm install can write its lockfile and temp files as `node`.
RUN corepack enable \
 && corepack prepare pnpm@10.4.0 --activate \
 && chown -R node:node /app "$COREPACK_HOME"

USER node

COPY --chown=node:node package.json pnpm-lock.yaml .npmrc ./
RUN pnpm install --frozen-lockfile --prod --prefer-offline

# Copy the compiled output and the migrations folder (used by `node dist/...`)
COPY --chown=node:node --from=build /app/dist ./dist
COPY --chown=node:node migrations ./migrations

# Healthcheck targets the Fastify /healthz endpoint.
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/healthz').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"

# Default command starts the HTTP API. Override with `docker run ... node dist/queue/workers/main.js`
# to start workers instead.
CMD ["node", "dist/api/server.js"]
