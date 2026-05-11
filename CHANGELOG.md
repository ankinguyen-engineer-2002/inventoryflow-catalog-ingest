# Changelog

Notable changes to this submission. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Final — submission lead document

- Added `SUBMISSION.md` at repo root as the primary entry point for
  reviewers. One page summarising what the test asked for, what was
  delivered, how to run it in three commands, and verification queries.
- Updated `README.md` primary callout to lead with `SUBMISSION.md`.
  Deeper documents (TRACK_A.md, PLAN.md, ADRs) remain as optional
  architectural review material linked from SUBMISSION.md.
- Rationale: the test specification explicitly weights pragmatism and
  speed. Leading the repository with a concise summary respects that
  while preserving the depth required to demonstrate senior judgment
  for those reviewers who want it.

### Day 5 — Track A documentation consolidated

- Single canonical `docs/TRACK_A.md` consolidates all Track A documentation:
  architecture, data pipelines, configuration, verification, scaling
  roadmap (phases two and three), free-at-scale deployment patterns,
  operational concerns, trade-offs, migration path to Track B,
  command reference, environment variables, and query cookbook.
- Removed `docs/RECRUITER_GUIDE.md`; its content is integrated into
  Section 7 (Configuration) and Section 8 (Verification) of TRACK_A.md.
- Updated README.md primary navigation to point to TRACK_A.md as the
  single Track A reference.

### Day 4 — 2026-05-11 (Track A complete)

**M4 — LLM enrichment wired into the data pipeline.**

- New CLI: `pnpm enrich --mode audit --limit N` — cross-validates the
  current `products.name_en` against a fresh LLM translation of
  `products.name_cn`, records `translation_consensus` (agree / partial /
  disagree) into `data_quality` JSONB. This is Layer 3 of the five-layer
  accuracy framework documented in ADR-007.
- 68 products audited on the sample file; 16% disagreement rate caught
  real OEM data defects ("busher" typo, "flat gasket" vs "flat washer",
  "front fork" vs "front shock absorber"). The dealer-supplied EN is
  preserved as the source of truth; the LLM alternative lives in
  `data_quality.translation_llm_alt` for a downstream review step.
- Three new providers implemented end-to-end (replacing Day 3 stubs):
  `OllamaProvider` (local qwen2.5:7b, $0), `AnthropicBatchProvider`
  (cloud, paid; documented but not invoked), `claude-code-handoff`
  refined (the one I use to seed the cache via my Claude Max session).
- `gemini-free-tier` intentionally stays stubbed: Gemini's TOS allows
  Google to train on API content, which is a privacy risk for dealer
  catalog data in production.
- `shared/llm-cache.jsonl` seeded with 51 verified translation entries
  and committed. Reviewer runs `pnpm enrich` against the cache and pays
  $0 in API spend.
- Cache decorator hardened: never caches null results (avoids freezing
  the pipeline on pending handoff tasks); sorted-key JSON serialisation
  for stable cache keys; new unit tests for cache hit / cache miss /
  distinct-inputs.
- New docs/TRACK_A.md (engineering write-up, senior voice) and
  docs/RECRUITER_GUIDE.md (concrete review commands, no guessing).
- README updated: M0-M4 all complete; Track B explicitly scoped as
  documented-not-implemented.

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
