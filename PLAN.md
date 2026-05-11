# InventoryFlow Catalog Ingest — Engineering Plan (v2)

| Field            | Value                                                                 |
| ---------------- | --------------------------------------------------------------------- |
| Document         | Master Plan — single source of truth before code                      |
| Version          | **v2** — Track B switched to Dagster + Iceberg + Redpanda + RisingWave; batch + near-realtime streaming hybrid added to both tracks; v10 control-plane capability matrix added |
| Author           | Aric Nguyen                                                           |
| Audience         | Talemy Hiring Team / InventoryFlow founder                            |
| Status           | DRAFT v2 — pre-implementation                                         |
| Created          | 2026-05-11                                                            |
| Test reference   | Talemy x InventoryFlow Senior Engineer Test (2026-05-08)              |
| Input dataset    | `Copy of Example Data for Engineer.xlsx` (241 MB, Kayo ATV OEM catalog) |

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement & Data Findings](#2-problem-statement--data-findings)
3. [Two-Track Strategy + Hybrid Workload Reality](#3-two-track-strategy--hybrid-workload-reality)
4. [Track A — JD-Native (TS Control Plane + Streaming Add-on)](#4-track-a--jd-native-ts-control-plane--streaming-add-on)
5. [Track B — Modern OSS DE (Dagster + Iceberg + Redpanda + RisingWave)](#5-track-b--modern-oss-de-dagster--iceberg--redpanda--risingwave)
6. [Side-by-Side Comparison](#6-side-by-side-comparison)
7. [AI/LLM Strategy — Zero-API-Cost Design](#7-aillm-strategy--zero-api-cost-design)
8. [Database Schema Design](#8-database-schema-design)
9. [Repository Structure](#9-repository-structure)
10. [Engineering Standards](#10-engineering-standards)
11. [Delivery Timeline (5 days)](#11-delivery-timeline-5-days)
12. [Risk Register](#12-risk-register)
13. [v10 Control Plane Capability Matrix](#13-v10-control-plane-capability-matrix)
14. [Open Questions for Recruiter](#14-open-questions-for-recruiter)
15. [Appendix A — Detailed Data Findings](#appendix-a--detailed-data-findings)
16. [Appendix B — Glossary](#appendix-b--glossary)

---

## 1. Executive Summary

### What this project does

Parses a 241 MB messy OEM Excel catalog (110 sheets, 1,586 embedded schematic images, EN+CN multilingual) into a clean PostgreSQL catalog with a JSONB `fitment` column and R2-hosted schematic images. **And** wires near-realtime streaming for downstream inventory/pricing/order events so the catalog isn't a stale weekly snapshot.

### How I'm delivering it

Two implementations in one monorepo, **both supporting batch + near-realtime streaming**:

- **Track A (recommended for submission)** — TypeScript + Node + PostgreSQL + Redis + BullMQ + R2, matching the JD's stack 1:1. Streaming added via Fastify webhooks + PG `LISTEN/NOTIFY` + optional Redpanda — **no JD-stack violation**. This is what InventoryFlow should run in production today (<500 dealers).
- **Track B (scale roadmap)** — Modern OSS DE: **Dagster** orchestrator + **Apache Iceberg** lakehouse + **Polars/DuckDB** compute + **Redpanda** event bus + **RisingWave** streaming SQL + **dbt-core** transformations. Vendor-neutral, asset-centric, lineage native. This is what InventoryFlow should migrate ingestion to once they hit ~500 dealers / 50 TB / 30% LLM cost share.

A `COMPARISON.md` quantifies trade-offs across 18 dimensions; `ADR-009` defines the migration triggers.

### Why this matters for the reviewer

The JD asks for "ownership, speed, and technical judgment over credentials" and "strong use (but not overuse) of AI tooling." This plan demonstrates:

- **Judgment** — picking the right stack for *current* stage, not the most impressive. Track A is the recommendation; Track B is documented optionality.
- **Modernity** — Track B uses the trendy 2025-2026 modern DE stack (Dagster + Iceberg) backed by clear technical reasoning, not bandwagoning.
- **Hybrid workload literacy** — both tracks handle batch (weekly xlsx) AND near-realtime (webhook/event) within one cohesive control plane.
- **Pragmatism** — every architectural choice has an ADR with an "AI suggestion vs my override" section.
- **AI tooling literacy** — provider abstraction layer + SQLite cache committed to repo means the reviewer can `git clone && pnpm ingest` with **zero API key and zero cost** while the code is structured for production-scale AI economics.
- **Operational rigor** — idempotent images via SHA-256 keys, dead-letter queue for failed rows, audit log per LLM call, replay-from-cache, schema migrations versioned, formal DR plan with RPO/RTO.
- **v10 senior signal** — explicit control-plane capability matrix (§13) showing which of 24 senior-grade capabilities are *implemented*, *deferred with rationale*, or *delivered free* by stack choice.

### Cost economics summary

| Run scenario                    | Track A cost        | Track B cost         |
| ------------------------------- | ------------------- | -------------------- |
| Reviewer running submission     | **$0** (cache hit)  | **$0** (cache hit)   |
| 1 dealer / one-shot ingest      | $3–5 LLM, $0 infra  | $3–5 LLM, $2 compute |
| 1000 dealers / month            | ~$1500 infra + LLM  | ~$400 (global dedupe) |
| Break-even (when B beats A)     | ~150–500 dealers, depending on streaming intensity            |

---

## 2. Problem Statement & Data Findings

### 2.1 What the JD asks for (decoded)

**Direct ask**: parse messy xlsx → clean Postgres + R2 + JSONB fitment.

**Implicit ask** (decoded from JD context):
- Idempotent re-ingestion (dealers re-send files weekly).
- Generalizes beyond the sample (hundreds of dealers, similar-but-different schemas).
- **Real-time inventory + pricing sync** to eBay/Amazon/Google Shopping (the actual revenue driver — Lightspeed DMS pushes inventory level changes, marketplace pulls catalog deltas).
- AI tooling demonstrated meaningfully.

### 2.2 Dataset reality (verified by parsing)

```
Copy of Example Data for Engineer.xlsx
├─ Size                     : 241 MB
├─ Sheets                   : 110
│   ├─ TOC                  : 1
│   ├─ Empty / junk         : 1 (Sheet18)
│   ├─ Parts catalog        : ~96 (48 models × 2 sheets — chassis + engine)
│   └─ Spec/reference       : ~12 (Carburetor Jets, Spark Plugs, Wheel/Fork…)
├─ Embedded images          : 1,586 (jpg + png) — ~246 MB total
├─ Languages                : English + Simplified Chinese (mixed per row)
└─ Inferred OEM             : Kayo (model code prefixes AY/AT/AU/K/T/S/eA)
```

**10 mess patterns found** (full table in Appendix A). Highlights:

1. Multi-section sheets, header row repeats 10–20×.
2. Schema drift between chassis (1 part-no column) and engine sheets (OLD+NEW).
3. `No.` column polymorphic: `1.0`, `"1-1"`, `"1-6L"`, `null`.
4. Same `No.` → multiple SKU variants (color, date-effective).
5. Multilingual cells with encoding artifacts.
6. 1586 images anchored via `xl/drawings/drawingN.xml`.
7. Year/make/model fitment encoded **in the sheet name**.
8. `make = "Kayo"` is nowhere in data — must be inferred.
9. Sheet names with trailing whitespace.
10. ~12 exception sheets with completely different schemas.

### 2.3 Goal output shape

```sql
SELECT part_number, name_en, name_cn, fitment, image_url
FROM products
WHERE fitment @> '[{"make":"Kayo","model":"Storm 150","year":2022}]';
```

Plus (Track B v2 new): streaming live view that updates within 1 second of dealer webhook:

```sql
-- RisingWave streaming materialized view
CREATE MATERIALIZED VIEW live_inventory AS
SELECT p.part_number, p.name_en, e.stock_level, e.updated_at
FROM iceberg.gold_products_mart p
JOIN redpanda_source('inventory.changes') e USING (part_number);
-- ↑ sub-second refresh on every webhook event
```

---

## 3. Two-Track Strategy + Hybrid Workload Reality

### 3.1 Why two tracks (unchanged from v1)

A single-track submission forces a binary choice that loses senior signal either way. Two tracks let the reviewer see:
- Stack fit (Track A is the JD-native deliverable).
- Scale judgment (Track B's modern lakehouse + streaming shows the migration path).
- Explicit recommendation for *current* stage with quantified triggers (ADR-009).

### 3.2 Hybrid workload — why both tracks need streaming

Catalog ingestion is **fundamentally batch** (dealers upload xlsx weekly). But the **downstream propagation** is **streaming**:

```
BATCH (slow, scheduled):                 STREAMING (fast, event-driven):
─────────────────────                    ────────────────────────────
• Dealer xlsx upload (weekly)            • Lightspeed DMS webhook
• Bulk pricing recalc (nightly)            (inventory level change)
• SHA-256 dedup audit                    • Marketplace event hooks
• OEM data sync (monthly)                  (order placed → stock-out)
                                         • Real-time pricing updates
                                         • CDC: Postgres → search index
                                         • New SKU push to marketplace
```

Without streaming, the catalog is a stale weekly snapshot — useless for real-time marketplace sync, which is the **actual revenue driver** mentioned in the JD ("syncing live inventory and listings to marketplaces like eBay and Amazon"). Both tracks must handle both modes.

### 3.3 What each track proves

| Track | Proves                                                                                                                  | Doesn't prove                              |
| ----- | ----------------------------------------------------------------------------------------------------------------------- | ------------------------------------------ |
| A     | Stack fit, ship velocity, pragmatic infra design, idempotency, observability, **streaming-within-JD-stack capability** | Lakehouse / distributed compute fluency    |
| B     | Schema evolution rigor, lineage discipline, modern OSS data platform fluency (Dagster + Iceberg), **unified batch+streaming via lakehouse format**, cost-at-scale economics | TS/Node ecosystem fluency                  |

---

## 4. Track A — JD-Native (TS Control Plane + Streaming Add-on)

### 4.1 Stack — exact JD match + streaming additions

| Layer                | Choice                            | Why / what's new vs v1                              |
| -------------------- | --------------------------------- | --------------------------------------------------- |
| Language             | TypeScript 5.x (strict)           | JD-mandated                                         |
| Runtime              | Node.js 22 LTS                    | JD-mandated                                         |
| HTTP framework       | Fastify                           | Schema validation, JD-friendly                      |
| Excel parser         | `exceljs` (streaming mode)        | Only Node lib streaming + drawings.xml              |
| XML parser           | `fast-xml-parser`                 | For `xl/drawings/*.xml`                             |
| ORM                  | Drizzle ORM                       | JSONB inference (ADR-004)                           |
| Database             | PostgreSQL 16                     | JD-mandated                                         |
| Queue (batch)        | BullMQ on Redis 7                 | JD-mandated                                         |
| **Webhook ingestion**| **Fastify `/events/*` routes**    | **NEW** — Lightspeed/eBay/dealer pushes             |
| **In-DB pub-sub**    | **PG `LISTEN/NOTIFY`**            | **NEW** — push DB changes to in-process consumers   |
| **CDC out (optional)**| **Debezium Postgres connector + Redpanda** | **NEW** — for downstream marketplace sync   |
| **Streaming queue**  | **BullMQ separate stream queues** | **NEW** — distinct conc=32 light-weight workers     |
| Object storage       | Cloudflare R2 (prod) / MinIO (dev)| Test mentions R2                                    |
| Validation           | Zod                               | Runtime + compile-time guarantees                   |
| Logging              | Pino                              | Structured JSON                                     |
| Tracing              | OpenTelemetry                     | Vendor-neutral                                      |
| Tests                | Vitest + Testcontainers           | TS-first                                            |
| Container            | Docker + docker-compose           | JD-mandated                                         |

**Streaming layer is opt-in via env flag** — `STREAMING_ENABLED=true` boots Redpanda container; off by default to keep `docker compose up` lean for reviewer.

### 4.2 Architecture — extended control plane (batch + streaming)

```
                ┌──────────────────────────────────────────────────────────────┐
                │  ⓪ INGRESS                                                   │
                │  BATCH:        CLI: pnpm ingest <file>                       │
                │                HTTP: POST /runs                              │
                │  STREAMING:    HTTP: POST /events/{inventory|pricing|order}  │
                │                Health: /healthz /readyz /metrics             │
                └──────────────────────────────┬───────────────────────────────┘
                                               │
                ┌──────────────────────────────▼───────────────────────────────┐
                │  ① CONTROL PLANE                                             │
                │  • Run Registry (ingest_runs UUID per batch)                 │
                │  • Event Registry (stream_events UUID per webhook event)     │
                │  • Job Scheduler — BullMQ queues + concurrency               │
                │  • Rate Limiter — Redis token bucket per upstream            │
                │  • Config Store — rules.yaml per dealer (versioned)          │
                │  • Secret Manager — env-scoped                               │
                │  • Tenant Resolver — header/JWT → dealer_id (multi-tenant)   │
                │  • Feature Flags — Unleash-OSS (deferred to ADR-011)         │
                └─────┬────────────────┬────────────────┬───────────────────────┘
                      │                │                │
        ┌─────────────▼─────┐  ┌───────▼──────────┐  ┌──▼──────────────────────┐
        │ ② BATCH WORKERS   │  │ ② STREAM WORKERS │  │ ③ INTELLIGENCE PLANE     │
        │ (heavy, conc=8)   │  │ (light, conc=32) │  │ (AI providers)           │
        │                   │  │                  │  │                          │
        │ q:parse-file      │  │ q:stream-inventory│  │ ILLMProvider interface   │
        │ q:parse-sheet     │  │ q:stream-pricing │  │  ├ MockProvider          │
        │ q:upload-image    │  │ q:stream-order   │  │  ├ ClaudeCodeHandoff…    │
        │ q:enrich-llm      │  │                  │  │  ├ OllamaProvider        │
        │ q:dlq             │  │ SLA: <500ms      │  │  ├ GeminiFreeProvider    │
        │                   │  │      event→DB    │  │  └ AnthropicBatch…       │
        │ SLA: <6min/file   │  │                  │  │                          │
        └─────────┬─────────┘  └────────┬─────────┘  │ CachedLLMProvider        │
                  │                     │            │  └ SQLite cache          │
                  └──────────┬──────────┘            └─────────┬────────────────┘
                             │                                 │
                             └────────────────┬────────────────┘
                                              │
                ┌─────────────────────────────▼──────────────────────────────┐
                │  ④ STORAGE PLANE                                           │
                │  • PostgreSQL 16 (catalog + JSONB fitment + LISTEN/NOTIFY) │
                │  • Cloudflare R2 / MinIO (schematic images SHA-256)        │
                │  • Redis 7 (queue + rate-limit + cache + pub/sub)          │
                │  • Local SQLite (LLM response cache committed in repo)     │
                │  • [Optional] Redpanda topics: postgres.cdc, inventory.changes, │
                │    pricing.updates — when STREAMING_ENABLED=true            │
                └─────────────────────────────┬──────────────────────────────┘
                                              │
                ┌─────────────────────────────▼──────────────────────────────┐
                │  ⑤ OBSERVABILITY PLANE                                     │
                │  • Pino logs (JSON, run_id + event_id + dealer_id corr.)   │
                │  • Prometheus metrics: rows/sec, llm_$, dlq_size,          │
                │    event_lag_ms (p50/p95/p99), webhook_5xx_rate            │
                │  • OpenTelemetry traces: 1 trace per run/event             │
                │  • Audit table: every LLM call (prompt+cost)               │
                │  • Lineage: row → section → sheet → file → run             │
                │           OR  event → topic → consumer → DB write          │
                └────────────────────────────────────────────────────────────┘
```

### 4.3 Data flow — single ingest run (batch) + single webhook (streaming)

```
BATCH FLOW (unchanged from v1):
  FILE.xlsx → POST /runs → q:parse-file → 110× q:parse-sheet (conc=8) →
  q:upload-image + q:enrich-llm → Postgres + R2 → status=SUCCESS

STREAMING FLOW (NEW):
  Lightspeed webhook
       │
       ▼
  POST /events/inventory          ← Fastify route, JWT-verify dealer_id
       │
       ▼  publish JSON body
  q:stream-inventory (conc=32, in-memory rate-limit)
       │
       ▼  worker:
  • Zod validate event shape
  • UPSERT into products.inventory_jsonb_col (atomic merge)
  • INSERT stream_events table (audit)
  • pg_notify('inventory_change', json_build_object(...))   ← PG LISTEN/NOTIFY
       │
       ▼
  Subscribers (catalog API, marketplace sync worker) listen to 'inventory_change'
  channel via pg.connect.on('notification')
       │
       ▼
  Marketplace sync worker → eBay/Amazon API call within seconds

  [Optional]: If STREAMING_ENABLED=true →
  Debezium reads Postgres logical replication slot → publishes to Redpanda topic
  → external consumers can subscribe Kafka-style without touching the DB
```

### 4.4 Module breakdown

```
track-a-jd-native/
└── src/
    ├── ingest/                          # BATCH path
    │   ├── xlsx-reader.ts               # exceljs streaming wrapper
    │   ├── section-detector.ts          # header-regex section grouping
    │   ├── drawing-parser.ts            # xl/drawings/*.xml → image↔row
    │   ├── fitment-resolver.ts          # sheet name + TOC → fitment
    │   ├── row-normalizer.ts            # Zod schema, variant dedup
    │   └── rules.yaml                   # per-dealer schema overrides
    ├── streaming/                       # NEW — STREAMING path
    │   ├── webhook-router.ts            # Fastify routes /events/*
    │   ├── event-normalizer.ts          # Zod schemas per event type
    │   ├── pg-notify-publisher.ts       # publish PG NOTIFY
    │   ├── pg-listen-subscriber.ts      # listen + dispatch to handlers
    │   └── cdc-bridge.ts                # [optional] Debezium → Redpanda
    ├── ai/                              # unchanged
    │   ├── provider.ts
    │   ├── providers/
    │   ├── prompts/
    │   └── audit.ts
    ├── storage/
    │   ├── r2-uploader.ts
    │   └── db/
    │       ├── schema.ts                # Drizzle schema (includes new stream_events table)
    │       ├── client.ts
    │       └── repositories/
    │           ├── products.repo.ts
    │           ├── images.repo.ts
    │           ├── runs.repo.ts
    │           ├── audit.repo.ts
    │           └── stream-events.repo.ts # NEW
    ├── queue/
    │   ├── queues.ts                    # batch + stream queue defs
    │   ├── workers/
    │   │   ├── parse-file.worker.ts
    │   │   ├── parse-sheet.worker.ts
    │   │   ├── upload-image.worker.ts
    │   │   ├── enrich-llm.worker.ts
    │   │   ├── stream-inventory.worker.ts  # NEW
    │   │   ├── stream-pricing.worker.ts    # NEW
    │   │   ├── stream-order.worker.ts      # NEW
    │   │   └── dlq-replay.worker.ts
    │   └── rate-limiter.ts
    ├── api/
    │   ├── server.ts
    │   ├── routes/
    │   │   ├── runs.routes.ts
    │   │   ├── events.routes.ts          # NEW
    │   │   ├── health.routes.ts
    │   │   └── metrics.routes.ts
    │   └── plugins/
    │       ├── otel.plugin.ts
    │       └── multitenant.plugin.ts     # NEW — dealer_id resolver
    ├── cli/
    │   └── ingest.ts
    └── lib/
        ├── logger.ts
        ├── env.ts
        ├── otel.ts
        ├── errors.ts
        └── feature-flags.ts              # NEW — Unleash client stub
```

### 4.5 Performance tactics

| Bottleneck                          | Tactic                                                                |
| ----------------------------------- | --------------------------------------------------------------------- |
| 241 MB xlsx OOM on small node       | `exceljs.stream` async iterator, peak RAM <300 MB                     |
| 1,586 image uploads                 | `p-limit(16)` + R2 multipart + HEAD-skip by SHA-256                   |
| Postgres insert throughput          | `COPY FROM STDIN` batched 5000 rows; defer GIN until bulk done        |
| Same-dealer re-ingest               | `ON CONFLICT (part_number_norm, dealer_id) DO UPDATE`                 |
| LLM cost                            | Targeted only (~15%); Anthropic Batch (50% off); cache decorator      |
| Webhook-to-DB SLA <500ms            | Stream worker conc=32, in-memory rate limit, skip LLM in stream path  |
| Fitment lookup <50ms                | `GIN (fitment jsonb_path_ops)` index                                  |
| Marketplace sync lag                | PG LISTEN/NOTIFY pushes events; consumer reacts in same process       |

---

## 5. Track B — Modern OSS DE (Dagster + Iceberg + Redpanda + RisingWave)

### 5.1 Stack — trendy 2026, all OSS, vendor-neutral

| Layer                | Choice                          | Rationale (vs v1)                                                          |
| -------------------- | ------------------------------- | -------------------------------------------------------------------------- |
| Language             | Python 3.12                     | DE ecosystem standard                                                      |
| Local compute        | Polars + DuckDB                 | 5–30× faster than pandas; no JVM                                           |
| Distributed (future) | PySpark 3.5                     | Upgrade path for >5 GB files; not used in PoC                              |
| **Lakehouse format** | **Apache Iceberg** (pyiceberg)  | **CHANGED**: vendor-neutral, hot trend after Tabular acquisition + Snowflake Polaris. Delta was Databricks-tied. |
| **Orchestrator**     | **Dagster 1.x**                 | **CHANGED**: asset-centric paradigm matches medallion natively; lineage + DQ built-in; best-in-class asset graph UI (vs Prefect's flow-centric, Airflow's task-centric legacy). |
| Transformations      | dbt-core + dbt-duckdb           | Industry standard for SQL transformations                                  |
| **Data quality**     | **Dagster asset checks + dbt tests** | **CHANGED**: replaces Great Expectations bolt-on; built into orchestrator. |
| **Lineage**          | **Dagster asset graph (native) + OpenLineage emit** | **ENHANCED**: column-level lineage native, no bolt-on infra needed. |
| **Streaming bus**    | **Redpanda Community**          | **NEW**: Kafka API-compatible, no ZooKeeper, single binary, Rust core      |
| **Streaming SQL**    | **RisingWave**                  | **NEW**: incremental materialized views from Redpanda → Iceberg sink       |
| **CDC**              | **Debezium (Postgres connector)** | **NEW**: outbound change capture for marketplace propagation             |
| Analytics ad-hoc     | DuckDB                          | Sub-second queries on Iceberg gold                                         |
| Serving DB           | PostgreSQL 16 (gold-sync target)| Same as Track A — only ingestion changes, serving unchanged                |
| Container            | Docker + docker-compose         | postgres + minio + dagster-webserver + redpanda + risingwave + connect    |
| LLM providers        | Same `ILLMProvider` reused      | Single abstraction, Python port of Track A's TS interface                  |

**Pyiceberg caveat** [Need-verify]: write paths in pyiceberg 0.8+ are usable but less mature than delta-rs. For 241 MB PoC scale this is fine. Production at scale should use Spark+Iceberg or wait for pyiceberg 1.0. Documented in ADR-008.

### 5.2 Architecture — Dagster-orchestrated medallion lakehouse + streaming hybrid

```
                                  INPUT SOURCES
                ┌──────────────────────┬──────────────────────┐
                │ BATCH                │ STREAMING            │
                │ ─────                │ ─────────            │
                │ Dealer xlsx upload   │ • Lightspeed webhook │
                │ → S3 raw landing     │ • eBay/Amazon push   │
                │ (Dagster sensor      │ • Postgres CDC slot  │
                │  detects new file)   │   (Debezium)         │
                └──────────┬───────────┴──────────┬───────────┘
                           │                      │
                           ▼                      ▼
        ┌────────────────────────────┐  ┌──────────────────────────┐
        │ DAGSTER ASSETS (batch)     │  │ REDPANDA TOPICS          │
        │ ────────────────────       │  │ ────────────             │
        │ @asset bronze_catalog_rows │  │ • inventory.changes      │
        │ @asset silver_parts_atomic │  │ • pricing.updates        │
        │ @asset silver_fitment_     │  │ • orders.placed          │
        │   atomic                   │  │ • postgres.cdc.products  │
        │ @asset silver_images_meta  │  │                          │
        │ @asset gold_products_mart  │  │ Kafka-compatible API.    │
        │ @asset gold_marketplace_   │  │ No ZooKeeper. Single      │
        │   view                     │  │ binary. Free Community.  │
        │                            │  └──────────┬───────────────┘
        │ @asset_check (replaces GE) │             │
        │   ├ schema_match           │             ▼
        │   ├ no_null_part_number    │  ┌──────────────────────────┐
        │   ├ fitment_well_formed    │  │ RISINGWAVE               │
        │   └ price_in_range         │  │ (streaming SQL engine)   │
        │                            │  │ ─────────────────────    │
        │ @sensor on_new_xlsx        │  │ CREATE SOURCE redpanda_  │
        │   → materialize bronze     │  │   inventory FROM         │
        │ @sensor on_dealer_config   │  │   redpanda.broker        │
        │   → invalidate downstream  │  │                          │
        │                            │  │ CREATE MATERIALIZED VIEW │
        │ Asset Graph UI (built-in)  │  │   live_inventory AS      │
        │ → column-level lineage     │  │   SELECT p.*, e.stock_   │
        │   visualized               │  │     level FROM           │
        │ OpenLineage emit → DataHub │  │   iceberg.gold_products  │
        │                            │  │   _mart p JOIN           │
        └──────────┬─────────────────┘  │   redpanda_inventory e   │
                   │                    │   USING (part_number)    │
                   ▼                    │                          │
        ╔═══════════════════════════╗   │ SINK INTO                │
        ║ APACHE ICEBERG TABLES     ║◄──┤   iceberg.gold_live_view │
        ║ ─────────────────────     ║   │ (sub-second refresh)     │
        ║ bronze.catalog_rows       ║   └──────────┬───────────────┘
        ║ silver.parts_atomic       ║              │
        ║ silver.fitment_atomic     ║              │
        ║ silver.images_meta        ║              │
        ║ gold.products_mart        ║              │
        ║ gold.catalog_marketplace_view ◄──────────┘
        ║ gold.live_inventory_view  ║
        ║                           ║
        ║ Time travel: VERSION AS OF║
        ║ Schema evolution: native  ║
        ║ Vendor-neutral: Snowflake,║
        ║   Databricks, AWS, GCP,   ║
        ║   Cloudera all query      ║
        ║   from same catalog       ║
        ╚═════════════╤═════════════╝
                      │
              ┌───────┴───────┐
              ▼               ▼
        ┌──────────┐    ┌──────────────┐
        │PostgreSQL│    │DuckDB        │
        │(serving) │    │(analytics    │
        │          │    │ on Iceberg)  │
        │ dbt-     │    │              │
        │ postgres │    │ Sub-second   │
        │ sync     │    │ on 10M rows  │
        └──────────┘    └──────────────┘

  Batch SLA:        Dealer xlsx → gold mart <3 min (Polars wins read)
  Streaming SLA:    Webhook → live_inventory_view <1 sec
  CDC SLA:          PG change → marketplace sync <5 sec
  Lineage:          Column-level via Dagster asset graph + OpenLineage events
  Schema registry:  Iceberg catalog itself (built-in)
  DQ contracts:     Dagster asset checks (built-in, fails materialization)
```

### 5.3 Why this is "v10 senior-grade"

The Track B v2 stack delivers a number of v10 control-plane capabilities **for free** because they're built into the tools:

| v10 capability                  | How Track B v2 delivers it                                                |
| ------------------------------- | ------------------------------------------------------------------------- |
| Schema registry                 | Iceberg catalog (native)                                                  |
| Column-level lineage            | Dagster asset graph + OpenLineage emit                                   |
| Asset catalog/discovery         | Dagster asset graph UI; bridge to DataHub via OpenLineage                |
| Data quality contracts          | Dagster asset checks (fails materialization)                              |
| Schema evolution                | Iceberg `ALTER TABLE` + `mergeSchema`                                     |
| Time travel / replay            | Iceberg `VERSION AS OF` + Dagster backfills                              |
| Unified batch+streaming         | Iceberg as single source of truth, RisingWave writes to same tables       |
| Multi-tenant partitioning       | Iceberg partition spec `(dealer_id, ingestion_date)`                      |
| Vendor neutrality               | Iceberg + OSS Dagster + Redpanda Community → no lock-in                  |
| Cost attribution                | Dagster run metadata + asset materialization stats per partition          |

What's NOT free and must be built (deferred per ADR-011, ADR-012):
- Self-service dealer portal
- Policy engine (OPA)
- Formal feature flag system
- Multi-region replication

### 5.4 What's actually built in Track B PoC

PoC scope, not production. Day 3-4 of timeline:

- 1 Dagster repo with: 6 assets (bronze/silver×3/gold×2), 4 asset checks, 2 sensors.
- Polars script reads xlsx → writes Iceberg bronze on local MinIO via pyiceberg.
- 3 silver Iceberg tables; 2 gold via dbt-duckdb materialized on Iceberg.
- 1 Redpanda container with topic `inventory.changes`; sample event publisher script.
- 1 RisingWave container with materialized view consuming Redpanda → sinking to Iceberg.
- 1 sample DuckDB notebook querying gold.
- `make track-b-up` brings everything up; `make track-b-run` runs the batch path.

Out of scope:
- HTTP API (Track A owns serving).
- Full marketplace sync.
- DataHub / Marquez deploy (OpenLineage emits to stdout for PoC).
- Multi-dealer scenarios (single-tenant PoC, multi-tenant in ADR-011).

---

## 6. Side-by-Side Comparison

Full version in `docs/COMPARISON.md`. Summary — 18 dimensions:

| Dimension                            | Track A v2 (TS)             | Track B v2 (Modern OSS DE)        |
| ------------------------------------ | --------------------------- | --------------------------------- |
| **JD stack match**                   | ★★★★★ exact                 | ★★ different ecosystem            |
| **Time to ship (PoC)**               | 3 days                      | 4 days                            |
| **Batch wall-time, 241 MB**          | ~4–6 min                    | ~2–3 min (Polars)                 |
| **Streaming SLA, webhook→view**      | ~500 ms (PG NOTIFY)         | ~1 sec (RisingWave MV)            |
| **Wall-time, 100×1GB/file batch**    | Shard workers, ~3 h         | Native parallel, ~40 min          |
| **Wall-time, 10 TB historical**      | Hours-to-days               | <1 h (Iceberg time-travel)        |
| **Idempotency**                      | ★★★★ SHA-256 + upsert       | ★★★★★ Iceberg MERGE native        |
| **Replay / time travel**             | ★★ audit-log replay         | ★★★★★ `VERSION AS OF` native      |
| **Schema evolution**                 | ★★ manual Drizzle migration | ★★★★★ Iceberg native              |
| **Lineage (cell/column-level)**      | ★★★ audit table             | ★★★★★ Dagster + OpenLineage       |
| **Asset catalog/discovery**          | ★★ none built-in            | ★★★★★ Dagster asset graph         |
| **DQ contracts**                     | ★★ Zod runtime              | ★★★★★ Dagster asset checks        |
| **Cost / 1 dealer (LLM)**            | $3–5                        | $3–5                              |
| **Cost / 1000 dealers (LLM)**        | ~$3–5 k                     | ~$300–500 (global dedupe)         |
| **Infra cost / 1 dealer / month**    | ~$30                        | ~$80                              |
| **Infra cost / 1000 dealers / month**| ~$1500+                     | ~$400                             |
| **Team pickup**                      | ★★★★★ TS team trivial       | ★★ DE skill rare                  |
| **Vendor lock-in risk**              | ★★★★ low                    | ★★★★★ Iceberg → multi-vendor      |

**Recommendation**: Track A for current stage. Migrate ingestion to Track B at the triggers in ADR-009 (now updated for streaming SLA additions).

---

## 7. AI/LLM Strategy — Zero-API-Cost Design

(Unchanged from v1. See ADR-007.)

`ILLMProvider` interface + 5 implementations + SQLite cache committed to repo → reviewer runs zero-cost. `cached` provider is default; `claude-code-handoff` for me to generate cache; `anthropic-batch` is production target stub never invoked in submission.

---

## 8. Database Schema Design

### 8.1 Core tables (Postgres, both tracks share)

Same as v1 (`products`, `product_images`, `part_number_aliases`, `vehicle_models`, `ingest_runs`, `ingest_audit`, `reference_specs`) **plus new streaming tables**:

```sql
-- ============================================================
-- Stream event registry (NEW — Track A streaming path)
-- ============================================================
CREATE TABLE stream_events (
  event_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dealer_id       UUID NOT NULL,
  event_type      TEXT NOT NULL,    -- 'inventory'|'pricing'|'order'|...
  payload         JSONB NOT NULL,
  source          TEXT,             -- 'lightspeed'|'ebay'|'amazon'|'manual'
  received_at     TIMESTAMPTZ DEFAULT now(),
  processed_at    TIMESTAMPTZ,
  status          TEXT NOT NULL,    -- 'PENDING'|'PROCESSED'|'FAILED'
  error           TEXT
);

CREATE INDEX idx_stream_events_dealer_status_time
  ON stream_events (dealer_id, status, received_at DESC);

-- Outbox pattern for reliable propagation
CREATE TABLE stream_outbox (
  id              BIGSERIAL PRIMARY KEY,
  topic           TEXT NOT NULL,
  payload         JSONB NOT NULL,
  created_at      TIMESTAMPTZ DEFAULT now(),
  published_at    TIMESTAMPTZ,
  status          TEXT NOT NULL DEFAULT 'PENDING'
);

CREATE INDEX idx_outbox_pending ON stream_outbox (created_at)
  WHERE status = 'PENDING';
```

### 8.2 Iceberg schema (Track B)

```
bronze.catalog_rows
├── _run_id          : string
├── _dealer_id       : string
├── _source_path     : string
├── _source_sha256   : string
├── _source_sheet    : string
├── _row_index       : int
├── _ingested_at     : timestamp
└── _raw_json        : string

Partition: (_dealer_id, _ingestion_date)
Sort order: _source_sheet

silver.parts_atomic
├── part_number      : string
├── part_number_norm : string (sort key)
├── name_en          : string
├── name_cn          : string
├── ... (typed columns)

gold.products_mart
├── ... (same shape as Postgres products table, fitment as STRUCT[])

gold.live_inventory_view  ← written by RisingWave streaming sink
├── part_number      : string
├── dealer_id        : string
├── stock_level      : int
├── last_updated     : timestamp
```

---

## 9. Repository Structure

(Unchanged from v1 except: `track-b-data-engineering/orchestration/` becomes Dagster repo not Prefect flows; new `track-a-jd-native/src/streaming/`.)

```
inventoryflow-catalog-ingest/
├── PLAN.md                              # this file (v2)
├── README.md
├── CHANGELOG.md
├── LICENSE
├── .gitignore
│
├── docs/
│   ├── COMPARISON.md                    # 18-dimension matrix (updated)
│   ├── QUESTIONS_FOR_RECRUITER.md
│   ├── runbook.md
│   └── decisions/                       # ADRs (now 13 total)
│       ├── README.md
│       ├── ADR-001 two-track-monorepo
│       ├── ADR-002 jsonb-fitment
│       ├── ADR-003 sha256-idempotent-images
│       ├── ADR-004 drizzle-vs-prisma
│       ├── ADR-005 section-detection-strategy
│       ├── ADR-006 part-number-aliases
│       ├── ADR-007 llm-provider-cost-strategy
│       ├── ADR-008 medallion-iceberg-dagster   ← REWRITTEN
│       ├── ADR-009 when-to-switch-tracks
│       ├── ADR-010 batch-streaming-hybrid       ← NEW
│       ├── ADR-011 multi-tenant-isolation       ← NEW
│       ├── ADR-012 data-contracts-schema-reg    ← NEW
│       └── ADR-013 dr-bcp-rpo-rto               ← NEW
│
├── track-a-jd-native/                   # TS impl
│   ├── src/
│   │   ├── ingest/        (batch)
│   │   ├── streaming/     ← NEW
│   │   ├── ai/providers/
│   │   ├── storage/db/
│   │   ├── queue/workers/
│   │   ├── api/
│   │   ├── cli/
│   │   └── lib/
│   ├── test/
│   ├── migrations/
│   └── docker-compose.yml
│
├── track-b-data-engineering/            # Dagster + Iceberg + Redpanda + RisingWave
│   ├── dagster_project/                 ← Dagster code location
│   │   ├── assets/
│   │   │   ├── bronze.py
│   │   │   ├── silver.py
│   │   │   └── gold.py
│   │   ├── asset_checks.py
│   │   ├── sensors.py
│   │   ├── resources.py                 # IcebergIO, Redpanda, MinIO clients
│   │   └── definitions.py
│   ├── dbt/
│   │   ├── models/{bronze,silver,gold}/
│   │   ├── tests/
│   │   └── macros/
│   ├── streaming/
│   │   ├── risingwave_views.sql         # streaming SQL definitions
│   │   ├── redpanda_seed.py             # sample event publisher
│   │   └── connect/
│   │       └── debezium-postgres.json   # CDC connector config
│   ├── notebooks/
│   │   └── duckdb_demo.ipynb
│   ├── tests/
│   ├── pyproject.toml
│   ├── dbt_project.yml
│   └── docker-compose.yml               # pg + minio + dagster + redpanda + risingwave + kafka-connect
│
└── shared/
    ├── sample-data/
    ├── prompts/
    ├── schemas/
    └── llm-cache.sqlite
```

---

## 10. Engineering Standards

(Unchanged from v1 — see `docs/decisions/README.md` for ADR template, `docs/runbook.md` for ops conventions.)

Key points: Conventional Commits (no `feat: add X`), commit body = problem→diagnosis→fix→trade-off, ADR with "AI suggestion vs my override" for every decision, no Claude attribution in commits (HARD RULE per author preference).

---

## 11. Delivery Timeline (5 days)

Extended from 4 → 5 days to accommodate streaming layer + 4 new ADRs.

| Day | AM (≈4 h)                                                            | PM (≈4 h)                                                          | Checkpoint                                  |
| --- | -------------------------------------------------------------------- | ------------------------------------------------------------------ | ------------------------------------------- |
| 1   | docker-compose.yml + Drizzle schema + migrations. ADR-001, ADR-002.  | xlsx-reader + section-detector + drawing-parser (Track A batch). ADR-005. | M1: 1 sheet parsed to JSON                  |
| 2   | fitment-resolver + R2 uploader. ADR-003, ADR-004. Run 10 sheets→DB.  | BullMQ batch workers, full 110 sheets. ADR-006. Unit tests + bench. | M2: Full file → DB, idempotent re-run works |
| 3   | ILLMProvider + 5 impls + cache. ADR-007. Cache generation flow.       | **Track A streaming**: webhook routes + PG LISTEN/NOTIFY + stream workers. ADR-010 batch+streaming. | M3: Track A both modes runnable      |
| 4   | **Track B**: Dagster repo + Polars→Iceberg bronze on MinIO. Switch ADR-008 to Iceberg+Dagster. | dbt silver/gold + Dagster asset checks + sensor + 1 DuckDB notebook. | M4: Track B batch path runnable             |
| 5   | **Track B streaming**: Redpanda + RisingWave + sample event publisher. ADR-011 multi-tenant, ADR-012 data contracts, ADR-013 DR. | COMPARISON.md, README polish, full re-run both tracks, submission. | M5: Submission ready                        |

Buffer: 6 h unallocated (most likely: pyiceberg write-path edge cases, RisingWave config).

---

## 12. Risk Register

| Risk                                                              | Likelihood | Impact | Mitigation                                                      |
| ----------------------------------------------------------------- | ---------- | ------ | --------------------------------------------------------------- |
| Image-row anchor wrong on exception sheets                        | High       | Med    | Fallback: all images on sheet → all parts on sheet; flag in DQ  |
| exceljs OOM on 241 MB                                             | Low        | High   | Pre-extract xlsx as zip + read sheetN.xml via fast-xml-parser   |
| Claude Code handoff cache > 1 session                             | Med        | Low    | Batch tasks 50/session; resume via cache key                    |
| pyiceberg writes corrupt under concurrent writers                 | Med        | High   | PoC scope is single-writer; documented; production uses Spark   |
| RisingWave + Redpanda local Docker stack heavy                    | Med        | Med    | Make streaming opt-in via env flag; default off                 |
| Reviewer can't run 6-container compose                            | Med        | High   | `track-a` works standalone with 3 containers; Track B optional  |
| Dagster + Iceberg combo less battle-tested locally                | Med        | Med    | PoC fallback: emit OpenLineage stdout if catalog fails           |
| Schema migration mismatch local↔reviewer                          | Low        | High   | Drizzle migrate runs on container start                         |

---

## 13. v10 Control Plane Capability Matrix

This is the section that distinguishes a senior submission. **24 capabilities** of a true v10 control plane, with explicit status:

```
Status legend:  ✅ implemented   ⭐ delivered free by stack   📋 documented+deferred
                ⚠️ partial         ❌ not in scope
```

### 13.1 Capability matrix

| #  | Capability                              | Track A v2 | Track B v2 | Notes / ADR                                              |
| -- | --------------------------------------- | ---------- | ---------- | -------------------------------------------------------- |
| 1  | Run registry (immutable history)         | ✅          | ⭐ (Dagster runs) | `ingest_runs` table; Dagster run storage                  |
| 2  | Job scheduling + retry + DLQ             | ✅ BullMQ   | ⭐ Dagster   | per-queue rate-limits, exponential backoff               |
| 3  | Idempotency primitives                   | ✅ SHA-256  | ⭐ Iceberg MERGE | ADR-003                                                  |
| 4  | Audit log (every external call)          | ✅          | ✅          | `ingest_audit` table; works for both                     |
| 5  | Per-row provenance                       | ✅          | ✅          | source_file/sheet/row columns on every row               |
| 6  | Lineage (run-level)                      | ✅          | ⭐ Dagster   |                                                          |
| 7  | Lineage (column-level)                   | ⚠️ partial  | ⭐ Dagster + OpenLineage |                                                  |
| 8  | Schema registry (versioned)              | ⚠️ rules.yaml | ⭐ Iceberg catalog | ADR-012                                              |
| 9  | Data quality contracts                   | ⚠️ Zod runtime | ⭐ Dagster asset checks | ADR-012                                          |
| 10 | Multi-tenant isolation (config-level)    | ✅          | ✅          | `rules.yaml` per dealer + `dealer_id` partitioning       |
| 11 | Multi-tenant isolation (network/RBAC)    | 📋          | 📋          | ADR-011 — deferred until ≥50 dealers; sketched          |
| 12 | Secret management                        | ✅ env       | ✅ env       | Production: HashiCorp Vault or AWS Secrets Manager       |
| 13 | Health probes (`/healthz`, `/readyz`)    | ✅          | ⭐ Dagster   |                                                          |
| 14 | Metrics (Prometheus-compatible)          | ✅          | ⭐ Dagster + dbt + Redpanda |                                            |
| 15 | Distributed tracing (OpenTelemetry)      | ✅          | ⚠️ partial  | Dagster has OTel hooks; needs wiring                     |
| 16 | Cost attribution (per-run)               | ✅          | ⭐ Dagster metadata |                                                  |
| 17 | Cost attribution (per-tenant)            | 📋          | ⭐ Iceberg partition stats | ADR-011                                          |
| 18 | Feature flags                            | 📋          | 📋          | Unleash-OSS stub; deferred until needed                  |
| 19 | Circuit breakers / bulkheads             | ⚠️ partial  | ⚠️ partial  | BullMQ has rate-limits; Dagster has retry; full circuit-breaker (e.g., `opossum`) deferred |
| 20 | Time travel / replay                     | ⚠️ audit-replay | ⭐ Iceberg `VERSION AS OF` |                                           |
| 21 | Schema evolution                         | ⚠️ Drizzle migration | ⭐ Iceberg `ALTER TABLE` |                                           |
| 22 | Unified batch + streaming                | ✅          | ✅          | ADR-010 — the new big one                                |
| 23 | Self-service tenant portal               | ❌          | ❌          | UI work, out of backend test scope                       |
| 24 | Formal DR/BCP with RPO/RTO               | 📋          | 📋          | ADR-013 — RPO 5 min / RTO 1 h; runbook in `docs/runbook.md` |

### 13.2 Scoring

```
              Track A v2     Track B v2     Combined "best of both"
─────────────────────────────────────────────────────────────────
✅ implemented:      14            5              19
⭐ free by stack:    0             14             14
⚠️ partial:          5             3              5
📋 deferred ADR:     4             4              5
❌ out of scope:     1             2              1
─────────────────────────────────────────────────────────────────
Total covered:       19/24 (79%)  22/24 (92%)    33/24 = MAX
```

**Interpretation**:
- Track B v2 hits **92% of v10 capabilities**, vs the original Prefect+Delta plan's ~70%. The stack swap (Dagster + Iceberg) delivered 14 capabilities **free** (not built — inherited from tools).
- Track A v2 hits **79%** — short on column-level lineage, time travel, schema registry. Mitigated by being JD-native + simpler ops.
- The 5 "partial" rows are the senior-grade nuances I'd close in production via well-known patterns documented in their ADRs.

### 13.3 What this matrix proves to a senior reviewer

A junior submission lists tools. A senior submission **maps capabilities to tools + leaves explicit gaps with rationale**. The matrix lets the reviewer audit:

- Did the candidate understand what "control plane" means at scale?
- Did they pick tools that deliver capabilities free vs requiring bolt-ons?
- Did they document what's out of scope vs accidentally missing?
- Did they avoid over-engineering capabilities not yet needed?

---

## 14. Open Questions for Recruiter

(Unchanged from v1 — 5 in `docs/QUESTIONS_FOR_RECRUITER.md`. Plus one new from streaming addition:)

6. **Streaming events source confirmation**: the JD mentions "Lightspeed and others" DMS + eBay/Amazon. Is the **inbound webhook** event flow (Lightspeed → us, dealer → us) on-roadmap, or strictly outbound (us → marketplace)? My design supports both; current Track A streaming workers are inbound-focused. **Confirm direction**.

---

## Appendix A — Detailed Data Findings

(Unchanged from v1. See `docs/QUESTIONS_FOR_RECRUITER.md` for the 8-signal list of mess patterns caught while parsing.)

---

## Appendix B — Glossary

(Unchanged + new entries:)

| Term                | Definition                                                                 |
| ------------------- | -------------------------------------------------------------------------- |
| Asset (Dagster)     | A persisted data object; the Dagster equivalent of a "task output". Lineage between assets is the asset graph. |
| Asset check         | A Dagster construct that validates an asset post-materialization (replaces Great Expectations for many use cases). |
| Iceberg catalog     | The metadata store for Iceberg tables (REST, Glue, Nessie, etc.). Provides schema registry + ACID metadata commits. |
| Materialized view (RisingWave) | An incrementally-maintained SQL view that updates on each input event. Like a streaming index. |
| Outbox pattern      | A pattern where in-process events are written to a dedicated `outbox` table within the same transaction as business data, then asynchronously republished to a message bus. Guarantees at-least-once delivery. |
| LISTEN/NOTIFY       | PostgreSQL's built-in pub/sub: `NOTIFY channel, 'payload'` from one connection wakes all `LISTEN channel` consumers. Cheap, in-process, no external broker required. |
| CDC                 | Change Data Capture — extracting row-level changes from a database (typically via WAL/binlog) into an event stream. Debezium is the OSS standard implementation. |
| Kappa architecture  | A stream-first architecture where batch is just bounded streams. Track B v2 is "Kappa-lite via lakehouse format" — same Iceberg tables written by both batch and stream paths. |

---

**End of plan v2.** Next artifact: ADR-008 rewrite (Iceberg + Dagster), then ADR-010 (batch+streaming), then Day 1 PM code (`xlsx-reader.ts`).
