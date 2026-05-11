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

# ---------------------------------------------------------------------------
# Stage 1: dependency installation (cached on lockfile)
# ---------------------------------------------------------------------------
FROM node:${NODE_VERSION} AS deps
WORKDIR /app

# Enable pnpm via corepack (bundled with Node 22)
RUN corepack enable && corepack prepare pnpm@10 --activate

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

# corepack must run as root to symlink into /usr/local/bin. Drop privileges
# afterwards via USER node (uid 1000 pre-created on the alpine node image).
RUN corepack enable && corepack prepare pnpm@10 --activate

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
