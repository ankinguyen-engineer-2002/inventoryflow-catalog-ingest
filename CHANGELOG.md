# Changelog

Notable changes to this submission. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Plan v2 — 2026-05-11 (Day 0 evening)

**Major scope expansion** in response to senior-DE review:

- **Track B stack swap** (per ADR-008 v2): Prefect → Dagster, Delta Lake → Apache Iceberg, Great Expectations → Dagster asset checks. Stack is now 2026-modern + vendor-neutral.
- **Streaming layer added to both tracks** (ADR-010): Track A via Fastify webhooks + PG `LISTEN/NOTIFY` + outbox; Track B via Redpanda Community + RisingWave streaming SQL + Iceberg sinks.
- **Metadata-driven control plane** (ADR-014 — new): three registry tables (`dealers`, `ingestion_patterns`, `dealer_pattern_bindings`) drive generic dispatch engine. Onboards new dealer via INSERT, not deploy. Freshness-based scheduling replaces blind cron.
- **Multi-tenant isolation** (ADR-011 — new): Postgres RLS + dealer-prefixed R2 keys + per-tier BullMQ queues; bridge-model upgrade path documented.
- **Data contracts + schema registry** (ADR-012 — new): Zod runtime + Iceberg-as-registry + YAML cross-team contracts.
- **DR + BCP with RPO/RTO** (ADR-013 — new): explicit targets per service surface, restore drill cadence, incident response runbook.
- **v10 control plane capability matrix** added to PLAN.md §13: 24 senior-grade capabilities mapped per track with status (implemented/free-by-stack/partial/deferred/out-of-scope).
- **Timeline extended** 4 → 5 days to accommodate streaming layer + 4 new ADRs.
- README badges updated: Prefect → Dagster, Delta → Iceberg, added Redpanda + RisingWave + DuckDB + PG LISTEN/NOTIFY.
- COMPARISON.md expanded 16 → 18 dimensions.

### Added — 2026-05-11 (Day 0)

- Initial repo scaffold and PLAN.md
- Two-track monorepo structure (`track-a-jd-native/`, `track-b-data-engineering/`)
- 9 ADRs covering all major design decisions
- `docs/COMPARISON.md` — 16-dimension trade-off matrix
- `docs/QUESTIONS_FOR_RECRUITER.md` — open questions + assumptions + signals
- `docs/runbook.md` — operational reference
- `.gitignore`, `.env.example` (both tracks), `CHANGELOG.md`
- README files (root + per-track)

### Pending — Day 1

- `track-a-jd-native/src/ingest/*` (xlsx-reader, section-detector, drawing-parser)
- `track-a-jd-native/src/storage/db/schema.ts` (Drizzle schema)
- `track-a-jd-native/docker-compose.yml`
- First migration

### Pending — Day 2

- BullMQ workers, R2 uploader, full-file ingest run

### Pending — Day 3

- `ILLMProvider` + 5 providers + SQLite cache
- Track B PoC (Polars + Delta + dbt skeleton)

### Pending — Day 4

- Final README pass, submission
