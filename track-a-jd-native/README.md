# Track A — JD-Native (TypeScript Control Plane)

> The recommended implementation for InventoryFlow's current stage (<500 dealers).

**Stack**: TypeScript 5 · Node.js 22 LTS · Fastify · PostgreSQL 16 · Redis 7 · BullMQ · Cloudflare R2 (MinIO locally) · Drizzle ORM · exceljs · Zod · Pino · OpenTelemetry · Vitest · Docker

See [`../PLAN.md §4`](../PLAN.md#4-track-a--jd-native-typescript-control-plane) for the full architecture, data-flow diagrams, and performance tactics.

---

## Quick reference

```
src/
├── ingest/      xlsx streaming, section detection, drawings.xml, fitment resolution
├── ai/          ILLMProvider interface + 5 implementations + cache decorator
├── storage/     R2 uploader + Drizzle schema + repositories
├── queue/       BullMQ queue defs + workers + rate limiter
├── api/         Fastify routes: /runs, /healthz, /metrics
├── cli/         `pnpm ingest <file>` entrypoint
└── lib/         logger, env, otel, errors
```

## Run locally

```bash
cp .env.example .env
docker compose up -d           # postgres + redis + minio
pnpm install
pnpm db:migrate
pnpm ingest ../shared/sample-data/example.xlsx
```

Expected wall-time on M2 Mac, 241 MB input: **~4–6 min**, peak RAM **<200 MB**.

## Verify

```bash
# Run summary
psql -h localhost -U dev -d catalog -c 'SELECT * FROM ingest_runs ORDER BY started_at DESC LIMIT 1;'

# Sample fitment query
psql -h localhost -U dev -d catalog -c "
SELECT part_number, name_en, name_cn
FROM products
WHERE fitment @> '[{\"make\":\"Kayo\",\"model_code\":\"AY70-2\"}]'
LIMIT 5;"

# Image manifest
mc ls local/catalog/ | head
```

## Status

✅ **Solution A — end-to-end implemented.** `pnpm ingest:full` runs the complete pipeline (parser → 12-table Postgres + R2 + LLM audit) in ~60s. Sample output committed under `../sample-output/`. 32 unit tests pass. Bench numbers in `../docs/bench/bench-results.json`. See `../SUBMISSION.md` for the 3-command reviewer run, or the architecture truth-table at [`Inventoryflow_solution/docs/17-architecture-truth-table.md`](https://github.com/ankinguyen-engineer-2002/Inventoryflow_solution/blob/main/docs/17-architecture-truth-table.md) for which subsystems are implemented vs demo-only vs deferred.

## Design choices unique to Track A

- **Fastify over Express** — schema validation built-in, faster, integrates pino natively.
- **Drizzle over Prisma** — typed JSONB inference is cleaner for `fitment[]`; see [ADR-004](../docs/decisions/ADR-004-drizzle-vs-prisma.md).
- **exceljs streaming over `xlsx` (SheetJS)** — only Node lib that streams AND exposes drawings.xml without unzipping manually.
- **BullMQ over BeeQueue / Bull v3** — actively maintained, native DLQ, rate-limiting, OpenTelemetry hooks.
- **MinIO locally for R2** — same S3 SDK works against both; reviewer doesn't need a Cloudflare account.

## What's NOT in Track A

- Lakehouse / Delta tables (see Track B for analytics scale).
- Multi-region replication (single-region PG sufficient at current scale).
- Schema registry (single dealer schema per dealer config; future via Avro/Iceberg).
