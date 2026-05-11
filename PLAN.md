# InventoryFlow Catalog Ingest — Engineering Plan

| Field            | Value                                                                 |
| ---------------- | --------------------------------------------------------------------- |
| Document         | Master Plan (single source of truth before code)                      |
| Author           | Aric Nguyen                                                           |
| Audience         | Talemy Hiring Team / InventoryFlow founder                            |
| Status           | **DRAFT v1** — pre-implementation; updated as ADRs land               |
| Created          | 2026-05-11                                                            |
| Test reference   | Talemy x InventoryFlow Senior Engineer Test (2026-05-08)              |
| Input dataset    | `Copy of Example Data for Engineer.xlsx` (241 MB, Kayo ATV OEM catalog) |

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement & Data Findings](#2-problem-statement--data-findings)
3. [Two-Track Strategy Rationale](#3-two-track-strategy-rationale)
4. [Track A — JD-Native (TypeScript Control Plane)](#4-track-a--jd-native-typescript-control-plane)
5. [Track B — OSS Big-Data DE (Polars + Delta Lake Medallion)](#5-track-b--oss-big-data-de-polars--delta-lake-medallion)
6. [Side-by-Side Comparison Matrix](#6-side-by-side-comparison-matrix)
7. [AI/LLM Strategy — Zero-API-Cost Design](#7-aillm-strategy--zero-api-cost-design)
8. [Database Schema Design](#8-database-schema-design)
9. [Repository Structure](#9-repository-structure)
10. [Engineering Standards](#10-engineering-standards)
11. [Delivery Timeline & Milestones](#11-delivery-timeline--milestones)
12. [Risk Register](#12-risk-register)
13. [Open Questions for Recruiter](#13-open-questions-for-recruiter)
14. [Appendix A — Detailed Data Findings](#appendix-a--detailed-data-findings)
15. [Appendix B — Glossary](#appendix-b--glossary)

---

## 1. Executive Summary

### What this project does

Parses a 241 MB messy OEM Excel catalog (110 sheets, 1586 embedded schematic images, EN+CN multilingual text) into a clean PostgreSQL catalog with a JSONB `fitment` column and Cloudflare R2-hosted schematic images — the input shape InventoryFlow receives from dealers daily.

### How I'm delivering it

Two implementations in one monorepo:

- **Track A (recommended for submission)** — TypeScript + Node + PostgreSQL + Redis + BullMQ + Cloudflare R2, matching the JD's stack 1:1. This is what InventoryFlow should run in production today (<500 dealers).
- **Track B (scale roadmap)** — Polars + Delta Lake medallion + dbt + Prefect, all OSS. This is what InventoryFlow should migrate ingestion to once they hit ~500 dealers / ~50 TB historical, while keeping PostgreSQL as the serving layer.

A `COMPARISON.md` document quantifies the cost/perf/idempotency/team-pickup trade-offs across 16 dimensions so the reviewer can audit the decision.

### Why this matters for the reviewer

The JD asks for "ownership, speed, and technical judgment over credentials" and "strong use (but not overuse) of AI tooling". This plan demonstrates:

- **Judgment** — picking the right stack for *current* stage, not the most impressive one. Track A is the recommendation; Track B is documented optionality.
- **Pragmatism** — every architectural choice has an ADR with an "AI suggestion vs my override" section.
- **AI tooling literacy** — provider abstraction layer + SQLite cache committed to repo means the reviewer can `git clone && pnpm ingest` with **zero API key and zero cost** while the code is structured for production-scale AI economics.
- **Operational rigor** — idempotent images via SHA-256 keys, dead-letter queue for failed rows, audit log per LLM call, replay-from-cache, schema migrations versioned.

### Cost economics summary

| Run scenario                    | Track A cost        | Track B cost         |
| ------------------------------- | ------------------- | -------------------- |
| Reviewer running submission     | **$0** (cache hit)  | **$0** (cache hit)   |
| 1 dealer / one-shot ingest      | $3–5 LLM, $0 infra  | $3–5 LLM, $2 compute |
| 1000 dealers / month            | ~$1500 infra + LLM  | ~$400 (global dedupe) |
| Break-even (when B beats A)     | ~150–500 dealers, depending on file homogeneity                |

---

## 2. Problem Statement & Data Findings

### 2.1 What the JD asks for

> "Standardize \[OEM catalog data] all, give us a clean database that contains the schematic image uploaded into an R2 bucket, along with the part numbers, the English name, the Chinese name, and everything clean and organized. There should also be a JSON column that outlines every year, make, and model that the part fits."

**Implicit asks** (decoded from JD context):

- Idempotent re-ingestion (dealers re-send files weekly).
- Generalizes beyond the sample file (production has hundreds of dealers with similar-but-different schemas).
- Marketplace-friendly output (eBay/Amazon catalogs consume the JSONB).
- AI tooling demonstrated meaningfully, not as ornament.

### 2.2 Dataset reality (verified by parsing)

```
Copy of Example Data for Engineer.xlsx
├─ Size                     : 241 MB
├─ Sheets                   : 110
│   ├─ TOC                  : 1
│   ├─ Empty / junk         : 1 (Sheet18)
│   ├─ Parts catalog        : ~96 (48 models × 2 sheets — chassis + engine)
│   └─ Spec/reference       : ~12 (Carburetor Jets, Spark Plugs, Wheel/Fork
│                              /Battery/Spoke specs, Owners manuals, kits)
├─ Embedded images          : 1586 (jpg + png) — ~246 MB total
├─ Languages                : English + Simplified Chinese (mixed per row)
└─ Inferred OEM             : Kayo (model code prefixes AY/AT/AU/K/T/S/eA)
```

**Mess inventory (10 issues found by parsing 5 sample sheets)** — full table in [Appendix A](#appendix-a--detailed-data-findings):

1. Multi-section sheets: each sheet contains 10–20 sections, header row repeats (`No. | Part Number | EN name | CN name | Specifications in CN | Qty/vehicle | Dealer | QTY | Retail`).
2. Schema drift between chassis (1 part-number column) and engine sheets (OLD + NEW part-number columns for history rename).
3. `No.` column polymorphic: `1.0` (numeric), `"1-1"` (sub-assembly), `"1-6L"` (left/right variant), `null` (unnumbered part).
4. Same `No.` → multiple rows = SKU variants (color, date-effective).
5. Multilingual cells with encoding artifacts (e.g., `"军  绿"` — double space inside CN string).
6. 1586 images anchored via `xl/drawings/drawingN.xml` `<oneCellAnchor>` to row/col positions — must be parsed from XML, not just iterated.
7. Year/make/model fitment encoded **in the sheet name** (`"PREDATOR 125 (2016-2020)"`, `"Bull 180 AU180 (2020-2022)"`, `"AU180-2 (2023+)"`).
8. `make = "Kayo"` is **nowhere in the data** — must be inferred from model code or asked.
9. Sheet names with trailing whitespace, breaks naive key lookups.
10. ~12 "exception" sheets (Carburetor Jets, Owners Manuals) have completely different schemas.

### 2.3 Goal output shape

```sql
-- Conceptual (full schema in §8)
SELECT part_number, name_en, name_cn, fitment, image_url
FROM products
WHERE fitment @> '[{"make":"Kayo","model":"Storm 150","year":2022}]';

-- Returns rows like:
-- part_number   | 602006-0015
-- name_en       | black handle bar grip
-- name_cn       | 把套
-- fitment       | [{"year":2016,"make":"Kayo","model":"Predator 125",
--                  "model_code":"AT125-B","section":"Handlebars",
--                  "callout_no":"1"},
--                 {"year":2017,"make":"Kayo","model":"Predator 125", ...}]
-- image_url     | https://r2.dev/catalog/sha256/ab12cd34....jpg
```

---

## 3. Two-Track Strategy Rationale

### 3.1 Why two tracks, not one

A single-track submission forces a binary choice: **(a)** match the JD stack and ignore that I'd architect ingestion differently at scale, or **(b)** showcase a more scalable stack and risk looking like I can't fit their team. Senior engineering is about **knowing when each is right**, not picking a side.

Two tracks let the reviewer see:

- I can execute their stack (Track A is the deliverable, runs end-to-end).
- I understand its limits (Track B's PoC plus comparison matrix shows the failure mode at scale and the migration path).
- I made an explicit recommendation for *their* stage (Track A for now, switch criteria documented).

### 3.2 What each track proves

| Track | Proves | Doesn't prove                                |
| ----- | ------ | -------------------------------------------- |
| A     | Stack fit, ship velocity, pragmatic infra design, idempotency, observability | Familiarity with lakehouse/distributed compute |
| B     | Schema evolution discipline, lineage rigor, OSS data platform fluency, cost-at-scale economics | TS/Node ecosystem fluency                    |

### 3.3 What's in scope per track

| Concern                       | Track A                  | Track B                                                |
| ----------------------------- | ------------------------ | ------------------------------------------------------ |
| Excel parsing                 | Full implementation     | Full implementation                                    |
| Image upload to R2            | Full implementation     | Same code reused (shared module)                       |
| LLM enrichment                | Full implementation     | Same code reused (shared module)                       |
| PostgreSQL serving layer      | Primary store           | Gold-layer sync target                                 |
| Delta Lake bronze/silver/gold | —                       | Full implementation (small scale, on local MinIO)      |
| dbt transformations           | —                       | 3 silver + 2 gold models                               |
| Prefect orchestration         | —                       | 1 flow demoing DAG                                     |
| Production-grade DLQ/retries  | Yes (BullMQ)             | Demonstrated via Prefect retry policy                  |
| BullMQ workers                | Yes                      | —                                                      |
| HTTP API surface              | Yes (`/runs`, `/healthz`) | — (CLI only)                                          |

---

## 4. Track A — JD-Native (TypeScript Control Plane)

### 4.1 Stack — exact match to JD requirements

| Layer            | Choice                            | Why                                                                 |
| ---------------- | --------------------------------- | ------------------------------------------------------------------- |
| Language         | TypeScript 5.x (strict)           | JD-mandated                                                         |
| Runtime          | Node.js 22 LTS                    | JD-mandated; native fetch, fs/promises                              |
| HTTP framework   | Fastify                           | Faster than Express, schema validation via JSON Schema, native pino |
| Excel parser     | `exceljs` (streaming mode)        | Only Node lib that streams + exposes drawings.xml                   |
| XML parser       | `fast-xml-parser`                 | For `xl/drawings/*.xml` (image anchors)                             |
| ORM              | Drizzle ORM                       | Typed JSONB inference > Prisma; zero runtime overhead               |
| Migrations       | Drizzle Kit                       | Same toolchain as ORM                                               |
| Database         | PostgreSQL 16                     | JD-mandated                                                         |
| Queue            | BullMQ on Redis 7                 | JD-mandated; mature, OSS, has DLQ + rate-limit                      |
| Object storage   | Cloudflare R2 (S3-compatible)     | Test explicitly mentions R2                                         |
| S3 client        | `@aws-sdk/client-s3` v3           | R2 speaks S3                                                        |
| Validation       | Zod                               | Runtime + compile-time type guarantees                              |
| Logging          | Pino + pino-pretty (dev)          | Structured JSON, fastest in ecosystem                               |
| Tracing          | OpenTelemetry SDK                 | Vendor-neutral; can ship to Sentry/Honeycomb later                  |
| Tests            | Vitest                            | Native ESM, fast, TS-first                                          |
| Linting          | ESLint + Biome                    | Biome for speed, ESLint for ecosystem rules                         |
| Container        | Docker + docker-compose           | JD-mandated                                                         |
| Process manager  | none (let Docker handle restart)  | Avoid PM2 — simpler                                                 |

**Maturity**: every dependency above is GA. No preview/experimental features required.

### 4.2 Architecture — four-plane control plane

This is the same mental model used in production data platforms (Confluent Cloud, Databricks). Adapted for a single-tenant ingestion service.

```
                    ┌──────────────────────────────────────────────────┐
                    │  ⓪ INGRESS                                       │
                    │  • CLI:  pnpm ingest <xlsx-path>                 │
                    │  • HTTP: POST /runs  (body: { source_url, dealer_id }) │
                    │  • Health: /healthz /readyz /metrics             │
                    └──────────────────────┬───────────────────────────┘
                                           │
                    ┌──────────────────────▼───────────────────────────┐
                    │  ① CONTROL PLANE                                 │
                    │  ─────────────────                               │
                    │  • Run Registry  — ingest_runs table, run_id UUID │
                    │  • Job Scheduler — BullMQ queues + concurrency    │
                    │  • Rate Limiter  — Redis token bucket per upstream │
                    │  • Config Store  — rules.yaml per dealer (versioned) │
                    │  • Secret Manager — env-scoped (no plaintext disk) │
                    └─────────┬────────────────┬───────────────────────┘
                              │                │
                ┌─────────────▼─────┐  ┌───────▼──────────────────────┐
                │ ② DATA PLANE      │  │ ③ INTELLIGENCE PLANE         │
                │ (Workers)         │  │ (AI providers)               │
                │                   │  │                              │
                │ q:parse-sheet     │  │ ILLMProvider interface       │
                │  ├ exceljs stream │  │  ├ MockProvider              │
                │  ├ xml drawings   │  │  ├ ClaudeCodeHandoffProvider │
                │  ├ section detect │  │  ├ OllamaProvider            │
                │  ├ fitment resolve│  │  ├ GeminiFreeProvider        │
                │  └ Zod normalize  │  │  └ AnthropicBatchProvider    │
                │                   │  │                              │
                │ q:upload-image    │  │ CachedLLMProvider (decorator) │
                │  └ SHA256-key R2  │  │  └ SQLite cache (committed)  │
                │                   │  │                              │
                │ q:enrich-llm      │  │ Audit log: prompt+resp+cost  │
                │  └ Translate /    │  │                              │
                │    callout extract│  │                              │
                │                   │  │                              │
                │ q:dlq             │  │                              │
                │  └ Retry button   │  │                              │
                └─────────┬─────────┘  └───────┬──────────────────────┘
                          │                    │
                          └────────┬───────────┘
                                   │
                    ┌──────────────▼───────────────────────────────────┐
                    │  ④ STORAGE PLANE                                 │
                    │  • PostgreSQL 16  — catalog (products, fitment) │
                    │  • Cloudflare R2  — schematic images (SHA256)    │
                    │  • Redis 7         — queue + rate-limit + cache │
                    │  • Local SQLite    — LLM response cache (repo)  │
                    └──────────────┬───────────────────────────────────┘
                                   │
                    ┌──────────────▼───────────────────────────────────┐
                    │  ⑤ OBSERVABILITY PLANE                           │
                    │  • Pino logs (JSON, run_id correlation)          │
                    │  • Prometheus metrics: rows/sec, llm_$, dlq_size │
                    │  • OpenTelemetry traces: 1 trace per run         │
                    │  • Audit table: every LLM call (prompt+cost)     │
                    │  • Lineage: row → section → sheet → file → run  │
                    └──────────────────────────────────────────────────┘
```

### 4.3 Data flow — single ingest run

```
                FILE.xlsx
                    │
        ┌───────────▼────────────┐
        │ POST /runs             │ → returns run_id (UUID)
        │ → ingest_runs INSERT   │
        │   status=QUEUED        │
        └───────────┬────────────┘
                    │ enqueue q:parse-file
                    ▼
        ┌────────────────────────┐
        │ parse-file worker      │
        │ • exceljs.read stream  │
        │ • for each sheet:      │
        │     enqueue q:parse-sheet (fan-out 110)
        └───────────┬────────────┘
                    │
   ┌────────────────┼─────────────────┐
   ▼                ▼                 ▼  (8 workers parallel)
┌────────┐    ┌────────┐         ┌────────┐
│sheet 1 │    │sheet 2 │  ...    │sheetN  │
│worker  │    │worker  │         │worker  │
└───┬────┘    └───┬────┘         └───┬────┘
    │             │                  │
    │  per worker:                   │
    │  1. detect sections (header regex)
    │  2. parse drawings.xml → image↔section map
    │  3. for each row:
    │     a. Zod validate
    │     b. if name_en missing → enqueue q:enrich-llm
    │     c. UPSERT products ON CONFLICT (part_number_norm)
    │  4. for each image:
    │     enqueue q:upload-image
    │
    └─────────────┬────────────────────┘
                  ▼
       ┌──────────────────────┐
       │ q:upload-image       │  conc=16, R2 rate-limited
       │ • HEAD by SHA256     │
       │ • PUT if absent      │
       │ • INSERT product_images
       └──────────┬───────────┘
                  ▼
       ┌──────────────────────┐
       │ q:enrich-llm         │  conc=4, exp backoff
       │ • CachedLLMProvider  │
       │ • update products    │
       │ • audit_log INSERT   │
       └──────────┬───────────┘
                  ▼
       ┌──────────────────────┐
       │ Run completion check │
       │ • all queues empty?  │
       │ • UPDATE ingest_runs │
       │   status=SUCCESS     │
       │   stats={rows, $, t} │
       └──────────────────────┘
```

### 4.4 Module breakdown

```
track-a-jd-native/
└── src/
    ├── ingest/
    │   ├── xlsx-reader.ts          # exceljs streaming wrapper
    │   ├── section-detector.ts     # finds header rows, groups sections
    │   ├── drawing-parser.ts       # parses xl/drawings/*.xml → image↔row map
    │   ├── fitment-resolver.ts     # sheet-name + TOC → {year, make, model}
    │   ├── row-normalizer.ts       # Zod schema, variant dedup
    │   └── rules.yaml              # per-dealer schema overrides
    ├── ai/
    │   ├── provider.ts             # ILLMProvider interface
    │   ├── providers/
    │   │   ├── mock.provider.ts
    │   │   ├── cached.provider.ts          # decorator (SQLite)
    │   │   ├── claude-code-handoff.provider.ts
    │   │   ├── ollama.provider.ts
    │   │   ├── gemini-free.provider.ts
    │   │   └── anthropic-batch.provider.ts # production stub
    │   ├── prompts/
    │   │   ├── translate-cn-en.v1.txt
    │   │   ├── extract-callouts.v1.txt
    │   │   └── infer-make.v1.txt
    │   └── audit.ts                # log every call (prompt, response, cost)
    ├── storage/
    │   ├── r2-uploader.ts          # SHA256-keyed, idempotent
    │   └── db/
    │       ├── schema.ts           # Drizzle schema
    │       ├── client.ts           # connection pool
    │       └── repositories/
    │           ├── products.repo.ts
    │           ├── images.repo.ts
    │           ├── runs.repo.ts
    │           └── audit.repo.ts
    ├── queue/
    │   ├── queues.ts               # BullMQ queue definitions
    │   ├── workers/
    │   │   ├── parse-file.worker.ts
    │   │   ├── parse-sheet.worker.ts
    │   │   ├── upload-image.worker.ts
    │   │   ├── enrich-llm.worker.ts
    │   │   └── dlq-replay.worker.ts
    │   └── rate-limiter.ts         # Redis token bucket
    ├── api/
    │   ├── server.ts               # Fastify app
    │   ├── routes/
    │   │   ├── runs.routes.ts
    │   │   ├── health.routes.ts
    │   │   └── metrics.routes.ts
    │   └── plugins/
    │       └── otel.plugin.ts
    ├── cli/
    │   └── ingest.ts               # `pnpm ingest <file>`
    └── lib/
        ├── logger.ts
        ├── env.ts                  # Zod env validation
        ├── otel.ts
        └── errors.ts
```

### 4.5 Performance tactics

| Bottleneck                     | Tactic                                                                 |
| ------------------------------ | ---------------------------------------------------------------------- |
| 241 MB xlsx OOM on small node  | `exceljs.stream.xlsx.WorkbookReader` async iterator, peak RAM <200 MB  |
| 1586 image uploads             | `p-limit(16)` + R2 multipart for >5 MB; HEAD-skip if SHA-256 present   |
| Postgres insert throughput     | `COPY FROM STDIN` batched 5000 rows; defer GIN index until bulk done   |
| Same-dealer re-ingestion       | `ON CONFLICT (part_number_norm) DO UPDATE` (idempotent)                |
| LLM cost                       | Targeted only (~15% rows); Anthropic Batch (50% off) at prod; cache    |
| Query "fits model X year Y"    | `GIN (fitment jsonb_path_ops)` index; `@>` operator <50 ms on 10 M rows |
| Multi-dealer onboarding        | One BullMQ queue per dealer-tier; rate-limit upstream calls per dealer |
| Failure isolation              | Per-section transaction; failed rows → DLQ, not full-file rollback     |

### 4.6 Scaling story (when does Track A break?)

| Dealers / month | Track A status                          | Action                                    |
| --------------- | --------------------------------------- | ----------------------------------------- |
| 1–50            | Single-node Node + PG + Redis, $30/mo  | Stay                                      |
| 50–200          | Same, scale PG to 4 vCPU                | Stay                                      |
| 200–500         | Split workers to separate node, conn pool tuning | Stay, monitor PG bloat                    |
| 500–1000        | Postgres write contention on `products` table; LLM cost climbs | Either: PG partitioning + LLM dedup, or migrate ingestion to Track B |
| 1000+           | Single-PG write bottleneck                | Track B becomes economically required    |

---

## 5. Track B — OSS Big-Data DE (Polars + Delta Lake Medallion)

### 5.1 Why this exists (and why it's NOT the submission)

If InventoryFlow is growing 30%/week and projecting 1000+ dealers within 12 months, the Track A architecture eventually bottlenecks on PostgreSQL writes and re-ingestion cost. Track B demonstrates the migration target — an OSS lakehouse pattern that **doesn't lock into any vendor** (Databricks, Snowflake) but delivers the same scale economics.

The submission is **Track A end-to-end + a Track B working PoC** that proves the migration path is real, not theoretical.

### 5.2 Stack — OSS-only, no proprietary vendor

| Layer                | Choice                          | Why                                                                       |
| -------------------- | ------------------------------- | ------------------------------------------------------------------------- |
| Language             | Python 3.12                     | Strongest in DE ecosystem; my CV's home                                   |
| Local compute engine | **Polars** (default) + **DuckDB** (analytics) | Polars: 5–30× faster than pandas at this file size; zero JVM     |
| Distributed engine   | **PySpark 3.5** (only when >5 GB)    | Standard for true big-data; not used for 241 MB sample              |
| Storage format       | **Delta Lake 3.x** (delta-rs / Python) | Time travel, schema evolution, MERGE, ACID — without Databricks lock-in  |
| Object storage       | MinIO (local) / S3 / R2         | Same R2 path as Track A; MinIO for offline dev                            |
| Transformations      | **dbt-core** + dbt-postgres + dbt-duckdb | Industry-standard model layering, tests, docs                       |
| Orchestrator         | **Prefect 2.x** (or Dagster)    | DAG-as-code, OSS, better local DX than Airflow                            |
| Data quality         | **Great Expectations** + dbt tests | Schema enforcement at silver→gold boundary                              |
| Lineage              | **OpenLineage** spec → Marquez UI | Cell-level lineage, vendor-neutral                                       |
| Catalog              | (skip Hive/Unity) — Delta metadata only | KISS for OSS; can graduate to Polaris/Unity later                    |
| Serving DB           | PostgreSQL 16 (gold-mart sync)  | Same as Track A — Track B doesn't replace serving                         |
| Container            | Docker + docker-compose         | MinIO + Postgres + Prefect orion locally                                  |
| LLM providers        | Same `ILLMProvider` abstraction reused from Track A | Single source of truth, no duplication       |

**Why not Apache Iceberg?** Delta and Iceberg are roughly equivalent; chose Delta for **delta-rs (Rust)** mature Python bindings — no JVM needed for sample-scale work. Iceberg PyIceberg is improving but still pre-1.0 for write paths as of 2026-Q1.

**Why not Spark?** PySpark is overkill for 241 MB. Polars on a laptop processes this file faster than booting a Spark session. PySpark is documented as the upgrade for >5 GB files but **not used in the PoC**.

### 5.3 Architecture — Medallion lakehouse

```
                       ┌──────────────────────────────────────┐
                       │   ⓪ RAW LANDING (Object Storage)     │
                       │   s3://catalog-raw/                  │
                       │   └─ dealer={id}/run_id={uuid}/      │
                       │       ├─ source.xlsx                 │
                       │       └─ _manifest.json (SHA256)     │
                       └────────────────┬─────────────────────┘
                                        │ Prefect flow trigger
                       ┌────────────────▼─────────────────────┐
                       │   ① BRONZE — Raw Delta                │
                       │   bronze.catalog_rows                │
                       │   • 1 row per Excel data row, schemaless JSONB │
                       │   • Partition: dealer_id, ingestion_date │
                       │   • Z-ORDER: source_sheet            │
                       │   • Columns: _run_id, _source_path,  │
                       │              _row_index, _ingested_at, raw_json │
                       └────────────────┬─────────────────────┘
                                        │ Polars + delta-rs MERGE
                       ┌────────────────▼─────────────────────┐
                       │   ② SILVER — Conformed                │
                       │   silver.parts_atomic                │
                       │   silver.fitment_atomic              │
                       │   silver.images_meta                 │
                       │   silver.dealers                     │
                       │   silver.vehicle_models              │
                       │   • Schema enforced via Delta        │
                       │   • Great Expectations checks @ entry │
                       │   • SCD-2 friendly (effective_from/to) │
                       └────────────────┬─────────────────────┘
                                        │ dbt run
                       ┌────────────────▼─────────────────────┐
                       │   ③ GOLD — Business Marts             │
                       │   gold.products (denorm + fitment[]) │
                       │   gold.catalog_marketplace_view      │
                       │   gold.dealer_inventory_snapshot     │
                       │   • dbt models, dbt tests            │
                       │   • Documented via dbt docs site     │
                       └────────────────┬─────────────────────┘
                                        │
                       ┌────────────────▼─────────────────────┐
                       │   ④ CONSUMERS                         │
                       │   • PostgreSQL sync (catalog API)    │
                       │   • DuckDB analytics ad-hoc          │
                       │   • OpenSearch (marketplace search)  │
                       │   • Metabase / Power BI dashboards   │
                       └──────────────────────────────────────┘

      Orchestration:   Prefect flow per dealer × per file SHA-256
      Lineage:         OpenLineage events → Marquez (or DataHub)
      DQ gates:        Great Expectations at bronze→silver, silver→gold
      Image storage:   Same R2 bucket as Track A (shared module)
      Audit:           Delta time-travel + OpenLineage = full replay
```

### 5.4 Why this beats Track A at scale — concrete examples

| Real scenario                              | Track A response                                         | Track B response                                              |
| ------------------------------------------ | -------------------------------------------------------- | ------------------------------------------------------------- |
| Dealer re-sends file v2 (90% overlap)      | Re-process all rows, rely on upsert dedup                | Delta `MERGE INTO bronze ON _file_sha256` → only diff ingested |
| OEM adds new column "Country of Origin"    | Drizzle migration, redeploy                              | Delta `mergeSchema=true` → silent absorb + alert via OpenLineage |
| "Bug last night corrupted 10k fitment rows" | Restore from PG backup (point-in-time)                   | `bronze VERSION AS OF 47` → re-run silver/gold; xlsx not needed |
| Analytics: "most-changed part nos this Q"  | OLAP query on PG — slow at scale                         | DuckDB on Delta gold, partition-pruned, sub-second            |
| 1000 dealers global translate same `把套`  | LLM call per dealer ($$$)                                | Global `part_number_canonical` table → 1 translate ever       |
| Need to re-run last Tuesday's ingest exactly | Replay via DLQ + audit log — partial                    | `bronze AS OF '2026-05-04'` → bit-perfect rebuild              |

### 5.5 What's actually built in Track B PoC

Scope is **proof of concept**, not production. Day 3 PM of the timeline:

- 1 Prefect flow: `ingest_xlsx_flow(file_path, dealer_id)`
- Bronze: Polars reads xlsx → Delta write (partition by dealer_id) on local MinIO
- Silver: 3 dbt models (parts_atomic, fitment_atomic, images_meta) on duckdb-Delta
- Gold: 2 dbt models (products mart, catalog_marketplace_view) with dbt tests
- 1 Great Expectations suite at silver boundary
- 1 sample DuckDB query showing fitment lookup
- OpenLineage events emitted (stdout dump, no Marquez deploy)
- `pyproject.toml`, `dbt_project.yml`, all reproducible with `make track-b-up`

**Explicitly out of scope**: HTTP API, BullMQ-style queues, image upload (reuses Track A's TS module via FFI or rewrite).

---

## 6. Side-by-Side Comparison Matrix

Full version in `docs/COMPARISON.md`. Summary:

| Dimension                            | Track A (TS)                | Track B (OSS DE)                 |
| ------------------------------------ | --------------------------- | -------------------------------- |
| **JD stack match**                   | ★★★★★ exact                 | ★★ different ecosystem           |
| **Time to ship (PoC)**               | 2.5 days                    | 3.5 days                         |
| **Wall-time, 241 MB file, M2 Mac**   | ~4–6 min                    | ~2–3 min (Polars)                |
| **Wall-time, 100×1GB/file batch**    | Shard workers, ~3 hrs       | Native parallel, ~40 min         |
| **Wall-time, 10 TB historical replay** | Hours-to-days, write contention | <1 hr (Delta time-travel)     |
| **Idempotency**                      | ★★★★ SHA-256 + upsert       | ★★★★★ Delta MERGE primitive      |
| **Replay / time travel**             | ★★ audit log replay only    | ★★★★★ `VERSION AS OF` native     |
| **Schema evolution**                 | ★★ Drizzle migration manual | ★★★★★ `mergeSchema=true`         |
| **Lineage (cell-level)**             | ★★★ audit table              | ★★★★★ OpenLineage native          |
| **Observability**                    | ★★★★ pino + OTel + Prom     | ★★★★ Prefect UI + Marquez        |
| **Cost / 1 dealer (LLM)**            | $3–5                        | $3–5                              |
| **Cost / 1000 dealers (LLM)**        | ~$3–5 k                     | ~$300–500 (global dedupe)         |
| **Infra cost / 1 dealer / month**    | ~$30                        | ~$80                              |
| **Infra cost / 1000 dealers / month** | ~$1500+                    | ~$400 (object-storage economics)  |
| **Team pickup**                      | ★★★★★ TS team trivial       | ★★ DE skill is rare              |
| **Vendor lock-in risk**              | ★★★★ low (open libs)        | ★★★★ low (Delta + dbt OSS)       |
| **Schema drift detection**           | Manual via migrations        | Automatic, alerts on first ingest |

### Recommendation (also in `README.md`)

**Today: ship Track A.** It's the JD-correct answer for InventoryFlow's <500-dealer stage.
**At ~500 dealers OR ~50 TB historical OR when LLM cost > 30% of cloud bill**: migrate ingestion to Track B, keep PG as serving. ADR-009 documents the trigger criteria.

---

## 7. AI/LLM Strategy — Zero-API-Cost Design

### 7.1 Constraint

I (the candidate) will not pay for raw API access for this take-home. The reviewer will not be asked to pay either. Both tracks must run end-to-end with **zero API key** entered.

### 7.2 The `ILLMProvider` abstraction

All AI calls go through one TypeScript interface (and a Python mirror in Track B):

```ts
// track-a-jd-native/src/ai/provider.ts (skeleton)
export interface ILLMProvider {
  readonly name: string;
  translateCnToEn(cn: string, context?: PartContext): Promise<string>;
  extractCalloutsFromImage(imagePath: string): Promise<number[]>;
  inferMakeFromModelCode(modelCode: string): Promise<string>;
}
```

### 7.3 Five concrete implementations

| Provider                      | Use case                            | Cost   | Runtime requirement                                     |
| ----------------------------- | ----------------------------------- | ------ | ------------------------------------------------------- |
| `mock`                        | Unit tests, deterministic fixtures  | $0     | none                                                    |
| `cached` (decorator)          | Always-on wrapper, SQLite cache     | $0 (hit) | SQLite file committed to repo (`shared/llm-cache.sqlite`) |
| `claude-code-handoff`         | Dev / submission generation         | $0\*   | Operator runs Claude Code session (Aric's Max plan)     |
| `ollama-local`                | Offline batch, CN content           | $0     | `ollama serve` running with `qwen2-vl:7b` pulled        |
| `gemini-free-tier`            | Fallback, non-production data only  | $0     | `GEMINI_API_KEY` from free tier (15 req/min)            |
| `anthropic-batch`             | Production target                   | ~$0.003/row | `ANTHROPIC_API_KEY`, never run in submission        |

\* The session is the candidate's already-paid Claude Max subscription; no incremental cost.

### 7.4 How the reviewer runs the submission

```bash
git clone <repo>
cd inventoryflow-catalog-ingest/track-a-jd-native
cp .env.example .env  # LLM_PROVIDER=cached defaults to SQLite hit
docker compose up -d  # postgres + redis + minio (R2 stand-in)
pnpm install
pnpm db:migrate
pnpm ingest ../shared/sample-data/example.xlsx
# → ~5 min later: products, product_images, ingest_runs all populated
# → zero API keys entered, zero $ spent
```

The cache holds responses for the 200-ish LLM calls the pipeline needs. When the cache hits 100%, no upstream is invoked.

### 7.5 How I (Aric) generate the cache

```bash
# 1. Run with claude-code-handoff provider
LLM_PROVIDER=claude-code-handoff pnpm ingest example.xlsx
# → emits shared/handoff/translation_tasks.json with ~200 tasks

# 2. In my Claude Code session: "Read translation_tasks.json,
#    translate CN→EN for each, write to translation_results.json
#    matching by id"
# → Claude (me, this assistant) produces results.json

# 3. Re-run ingest; provider reads results, caches, finishes
LLM_PROVIDER=claude-code-handoff pnpm ingest example.xlsx

# 4. Commit shared/llm-cache.sqlite to repo
```

ADR-007 documents this as the right pattern for both this submission **and** production cost economics — the cache+decorator+provider triad is what InventoryFlow needs anyway when LLM cost dominates the bill.

### 7.6 What AI is used for (and what it is NOT used for)

**Used for:**
- Translating `name_en` when the cell is null/empty (~10–15% of rows).
- Extracting callout numbers from schematic images for cross-validation (audit-only, ~1% of images).
- Parsing the 12 "exception" sheets (Carburetor Jets, Owners Manuals) whose schema diverges.
- Inferring `make = "Kayo"` once per dealer (cached forever after).

**NOT used for:**
- Reading the structured rows of the 96 normal parts sheets. Those are deterministic; rule-based code handles them at sub-millisecond per row.
- Decision-making (which schema to apply, when to retry, what counts as a duplicate). Those are owned by code, not the model.
- Anything in the critical path that needs <50 ms latency.

### 7.7 LLM audit log

Every call is logged to `ingest_audit` table with: `run_id`, `provider`, `prompt_sha256`, `prompt_template_version`, `response_text`, `tokens_in`, `tokens_out`, `cost_usd`, `latency_ms`, `cache_hit` (bool). This means:

- Reviewer can audit exactly what was sent to which model and what was inferred.
- Production cost tracking is built in from day 1.
- Prompt regressions are detectable (template version bumps trigger cache invalidation).

---

## 8. Database Schema Design

### 8.1 Core tables (Postgres, used by both tracks)

```sql
-- ============================================================
-- Canonical product catalog
-- ============================================================
CREATE TABLE products (
  id                   BIGSERIAL PRIMARY KEY,
  part_number          TEXT NOT NULL,
  part_number_norm     TEXT GENERATED ALWAYS AS
                         (upper(regexp_replace(part_number, '\s', '', 'g'))) STORED,
  name_en              TEXT,
  name_cn              TEXT,
  spec_cn              TEXT,
  qty_per_vehicle      NUMERIC,
  dealer_cost          NUMERIC,
  unit                 TEXT,           -- "/ea"
  retail_price         NUMERIC,
  fitment              JSONB NOT NULL DEFAULT '[]'::jsonb,
  primary_image_r2_key TEXT,
  source_dealer_id     UUID,
  source_file_sha256   TEXT,
  source_sheet         TEXT,
  source_row_index     INT,
  data_quality         JSONB DEFAULT '{}'::jsonb,  -- {translations_source, callouts_verified, ...}
  created_at           TIMESTAMPTZ DEFAULT now(),
  updated_at           TIMESTAMPTZ DEFAULT now(),
  UNIQUE (part_number_norm, source_dealer_id)
);

-- ============================================================
-- Many-to-many: a part appears in N schematics
-- ============================================================
CREATE TABLE product_images (
  product_id     BIGINT REFERENCES products(id) ON DELETE CASCADE,
  r2_key         TEXT NOT NULL,
  r2_url         TEXT NOT NULL,
  sha256         TEXT NOT NULL,
  width_px       INT,
  height_px      INT,
  section_label  TEXT,             -- e.g., "CYLINDER HEAD/CYLINDER BODY"
  source_sheet   TEXT,
  PRIMARY KEY (product_id, sha256)
);

-- ============================================================
-- Part-number aliasing (handles OEM rename history)
-- ============================================================
CREATE TABLE part_number_aliases (
  product_id      BIGINT REFERENCES products(id) ON DELETE CASCADE,
  alias           TEXT NOT NULL,         -- old part number
  alias_norm      TEXT NOT NULL,
  alias_type      TEXT NOT NULL,         -- 'old', 'oem_alt', 'distributor'
  PRIMARY KEY (alias_norm, product_id)
);

-- ============================================================
-- Normalized vehicle dimension (for analytics joins)
-- ============================================================
CREATE TABLE vehicle_models (
  id          BIGSERIAL PRIMARY KEY,
  make        TEXT NOT NULL,
  model       TEXT NOT NULL,
  model_code  TEXT,
  category    TEXT,        -- 'SPORT_ATV' | 'UTILITY_ATV' | 'PITBIKE_EPA' ...
  year_start  INT,
  year_end    INT,         -- null if open-ended ("2023+")
  variant     TEXT,        -- 'EPA' | 'D' | etc.
  UNIQUE (make, model_code, year_start, year_end, variant)
);

-- ============================================================
-- Run registry (one row per ingest invocation)
-- ============================================================
CREATE TABLE ingest_runs (
  run_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dealer_id         UUID,
  source_file       TEXT NOT NULL,
  source_sha256     TEXT NOT NULL,
  status            TEXT NOT NULL,    -- QUEUED|RUNNING|SUCCESS|FAILED|PARTIAL
  rows_attempted    INT,
  rows_succeeded    INT,
  rows_failed       INT,
  llm_calls         INT,
  llm_cost_usd      NUMERIC(10,4),
  started_at        TIMESTAMPTZ DEFAULT now(),
  finished_at       TIMESTAMPTZ,
  error             TEXT
);

-- ============================================================
-- LLM audit log (every call ever)
-- ============================================================
CREATE TABLE ingest_audit (
  id                  BIGSERIAL PRIMARY KEY,
  run_id              UUID REFERENCES ingest_runs(run_id),
  provider            TEXT NOT NULL,
  prompt_sha256       TEXT NOT NULL,
  prompt_template_ver TEXT NOT NULL,
  response_text       TEXT,
  tokens_in           INT,
  tokens_out          INT,
  cost_usd            NUMERIC(10,6),
  latency_ms          INT,
  cache_hit           BOOLEAN NOT NULL,
  created_at          TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- Reference specs (the ~12 weird sheets — Carburetor Jets etc.)
-- ============================================================
CREATE TABLE reference_specs (
  id            BIGSERIAL PRIMARY KEY,
  category      TEXT NOT NULL,    -- 'carburetor_jets' | 'spark_plugs' | ...
  model_code    TEXT,             -- nullable; some are universal
  attributes    JSONB NOT NULL,   -- free-form
  source_sheet  TEXT,
  source_row    INT
);

-- ============================================================
-- Indexes
-- ============================================================
CREATE INDEX idx_products_fitment_gin        ON products USING gin (fitment jsonb_path_ops);
CREATE INDEX idx_products_name_en_trgm       ON products USING gin (name_en gin_trgm_ops);
CREATE INDEX idx_products_name_cn_trgm       ON products USING gin (name_cn gin_trgm_ops);
CREATE INDEX idx_products_part_number_norm   ON products (part_number_norm);
CREATE INDEX idx_aliases_norm                ON part_number_aliases (alias_norm);
CREATE INDEX idx_runs_status_started         ON ingest_runs (status, started_at DESC);
CREATE INDEX idx_audit_run                   ON ingest_audit (run_id, created_at);
```

### 8.2 The JSONB fitment shape (test's stated focus)

```json
[
  {
    "year": 2016,
    "make": "Kayo",
    "model": "Predator 125",
    "model_code": "AT125-B",
    "variant": null,
    "category": "SPORT_ATV",
    "section": "Front Brake Assembly",
    "callout_no": "1-1",
    "callout_verified": true,
    "confidence": "high"
  },
  {
    "year": 2017,
    "make": "Kayo",
    "model": "Predator 125",
    "model_code": "AT125-B",
    "variant": null,
    "category": "SPORT_ATV",
    "section": "Front Brake Assembly",
    "callout_no": "1-1",
    "callout_verified": true,
    "confidence": "high"
  }
]
```

**Why an array of denormalized objects vs a join table?** See ADR-002. Summary: matches the test's literal ask, marketplace consumers eat the JSON directly without server-side joins, GIN `jsonb_path_ops` makes `@>` lookups <50 ms on 10 M rows, denormalization is fine here because fitment is read-heavy/low-update.

### 8.3 Track B Delta schema (bronze)

```
bronze.catalog_rows
├── _run_id              : string (UUID)
├── _dealer_id           : string
├── _source_path         : string
├── _source_sha256       : string
├── _source_sheet        : string
├── _row_index           : int
├── _ingested_at         : timestamp
├── _raw_json            : string  (entire row as JSON, schemaless safety net)
└── (typed columns added by silver, not here)

Partitioning : (_dealer_id, _ingestion_date)
Z-ORDER      : _source_sheet
```

---

## 9. Repository Structure

```
inventoryflow-catalog-ingest/
│
├── PLAN.md                              # this file
├── README.md                            # 1-page entry; links to PLAN + tracks
├── CHANGELOG.md
├── .gitignore
│
├── docs/
│   ├── COMPARISON.md                    # 16-dimension matrix
│   ├── QUESTIONS_FOR_RECRUITER.md       # assumptions + clarifications
│   ├── runbook.md                       # ops: how to run, how to debug
│   ├── decisions/                       # ADRs
│   │   ├── README.md                    # ADR index
│   │   ├── ADR-001-two-track-monorepo.md
│   │   ├── ADR-002-jsonb-fitment.md
│   │   ├── ADR-003-sha256-idempotent-images.md
│   │   ├── ADR-004-drizzle-vs-prisma.md
│   │   ├── ADR-005-section-detection-strategy.md
│   │   ├── ADR-006-part-number-aliases.md
│   │   ├── ADR-007-llm-provider-cost-strategy.md
│   │   ├── ADR-008-medallion-architecture-track-b.md
│   │   └── ADR-009-when-to-switch-tracks.md
│   ├── bench/                           # benchmark notebooks/scripts
│   └── diagrams/                        # exported architecture PNGs
│
├── track-a-jd-native/                   # TypeScript implementation
│   ├── README.md
│   ├── package.json
│   ├── tsconfig.json
│   ├── docker-compose.yml               # postgres + redis + minio
│   ├── drizzle.config.ts
│   ├── .env.example
│   ├── src/
│   │   ├── ingest/                      # xlsx parsing, section detection
│   │   ├── ai/
│   │   │   ├── providers/               # mock, cached, ollama, ...
│   │   │   └── prompts/
│   │   ├── storage/
│   │   │   └── db/                      # Drizzle schema + repos
│   │   ├── queue/
│   │   │   └── workers/
│   │   ├── api/                         # Fastify HTTP surface
│   │   ├── cli/                         # `pnpm ingest`
│   │   └── lib/
│   ├── test/
│   │   ├── unit/
│   │   ├── integration/
│   │   ├── benchmark/
│   │   └── fixtures/
│   ├── migrations/                      # Drizzle SQL
│   └── scripts/                         # one-off ops scripts
│
├── track-b-data-engineering/            # Polars + Delta + dbt
│   ├── README.md
│   ├── pyproject.toml
│   ├── docker-compose.yml               # postgres + minio + prefect
│   ├── dbt_project.yml
│   ├── profiles.yml
│   ├── .env.example
│   ├── pipelines/                       # Polars ingestion code
│   │   ├── bronze/
│   │   ├── silver/
│   │   └── gold/
│   ├── dbt/
│   │   ├── models/
│   │   │   ├── bronze/                  # source declarations
│   │   │   ├── silver/                  # conformed
│   │   │   └── gold/                    # marts
│   │   ├── tests/                       # dbt tests + GE
│   │   ├── macros/
│   │   └── seeds/                       # static reference (TOC categories, etc.)
│   ├── orchestration/                   # Prefect flows
│   ├── notebooks/                       # exploratory + DuckDB demos
│   ├── tests/                           # pytest
│   └── scripts/
│
├── shared/                              # reused across tracks
│   ├── README.md
│   ├── sample-data/
│   │   └── README.md                    # links to the xlsx (not committed)
│   ├── prompts/                         # canonical prompt templates
│   │   └── README.md
│   ├── schemas/                         # JSON schemas / OpenAPI fragments
│   └── fixtures/                        # cached LLM responses (SQLite)
│
└── scripts/                             # repo-level setup/teardown
    └── (setup.sh, generate-cache.sh, ...)
```

Total: 37 directories, ~50–80 source files when fully implemented (estimate based on Drizzle/dbt patterns).

---

## 10. Engineering Standards

These standards apply to both tracks. They are how I demonstrate that AI tooling accelerated the work but did not replace the engineer.

### 10.1 Commit hygiene

- Conventional Commits subject (`feat:`, `fix:`, `refactor:`, `chore:`, `docs:`).
- Body: **problem → diagnosis → fix → trade-off** (4 paragraphs max).
- No AI-default messages (`feat: add functionality`). Every commit body answers "why now and why this way".
- Examples in `docs/runbook.md`.

### 10.2 Code review markers

Comments use these tags for non-obvious decisions:

- `// REJECTED: <alternative> — <reason>` — anti-pattern killed in PR.
- `// NOTE: LLM suggested X; overridden because Y` — visible AI override.
- `// PERF: …` — anything justifying a perf-driven choice.
- `// SAFETY: …` — invariant a future reader could miss.

Generic comments explaining what code does are removed.

### 10.3 ADR discipline

Every architectural decision lands an ADR before code. ADR template (`docs/decisions/`):

```markdown
# ADR-NNN: <Title>

## Status
Accepted | Superseded | Deprecated

## Context
2–4 paragraphs of the problem.

## Decision
What we're doing. One paragraph.

## AI suggestion vs my override
What the LLM (Claude/Cursor) initially proposed.
What I chose instead and the concrete reason (with link to evidence
— benchmark, code sample, doc reference).

## Trade-offs accepted
Bulleted.

## When to revisit
Concrete trigger condition.

## Sources
Doc links (versioned, dated).
```

Minimum 9 ADRs planned for v1 (see `docs/decisions/`).

### 10.4 Test pyramid

```
Pyramid (Track A target):
├── Unit (Vitest)          : >60 tests, sub-second total
├── Integration (Testcontainers PG/Redis) : ~10 tests, ~30s total
├── Benchmark (Vitest bench): 3 tests (insert throughput, R2 upload, xlsx parse)
└── E2E (manual + fixture)  : 1 golden run on AY70-2 with snapshot

Pyramid (Track B target):
├── pytest unit             : ~20 tests on transforms
├── dbt tests               : every silver/gold model
├── Great Expectations      : 1 suite at silver entry
└── E2E (Prefect deploy local): 1 happy-path flow
```

### 10.5 Observability minimums

- Every log line has `run_id` correlation.
- Every queue job emits start/finish span.
- Prometheus metrics scrape on `/metrics` (Track A).
- Failure paths always log the row index + sheet name (no anonymous errors).

### 10.6 Security baseline

- No secrets in repo (`.env.example` only).
- LLM cache committed contains responses, never prompts that include sensitive data — verified by lint pass.
- All external HTTP traffic logged via OTel.
- R2 bucket access via scoped credentials, never root.
- Container runs non-root user.

---

## 11. Delivery Timeline & Milestones

Total budget: **4 working days** (32–36 hours).

| Day  | AM (≈4 h)                                       | PM (≈4 h)                                       | Checkpoint                                    |
| ---- | ----------------------------------------------- | ----------------------------------------------- | --------------------------------------------- |
| 1    | Repo skeleton (done in this session). `docker-compose.yml`. Drizzle schema + migrations. ADR-001, ADR-002. | Track A `xlsx-reader` + `section-detector` + `drawing-parser`. End-of-day: parse `AY70-2` sheet → JSON to console. ADR-005. | **M1**: 1 sheet end-to-end parsed (no DB yet) |
| 2    | Track A `fitment-resolver` + `row-normalizer` + R2 uploader (idempotent SHA-256). ADR-003. Run on 10 sheets → DB rows.    | Track A: BullMQ workers, run full 110 sheets → DB. Unit tests for section detection + No-column parsing. Benchmark insert throughput. ADR-004. | **M2**: Full file ingested, DB populated, idempotent re-run works |
| 3    | Track A: `ILLMProvider` + 5 implementations + cache decorator. `claude-code-handoff` flow tested. Generate LLM cache for the file. ADR-006, ADR-007. DLQ + `/healthz` + runbook draft. | Track B: Polars script reads xlsx → Delta bronze on MinIO. 1 dbt silver model. 1 dbt gold model. 1 Great Expectations suite. Sample DuckDB query in notebook. | **M3**: Both tracks runnable. Cache committed. |
| 4    | `COMPARISON.md` finalised with measured numbers. ADR-008, ADR-009. `QUESTIONS_FOR_RECRUITER.md` filled. Full README rewrite (engineering story).         | Polish: PR-style self-review of commit history. Fix any lingering edges. Re-run both tracks clean. Submit. | **M4**: SUBMITTED                              |

**Buffer**: 4 hours unallocated for unknown unknowns (most likely: image anchor mapping edge cases, LLM cache regeneration).

---

## 12. Risk Register

| Risk                                                              | Likelihood | Impact | Mitigation                                                                 |
| ----------------------------------------------------------------- | ---------- | ------ | -------------------------------------------------------------------------- |
| Image-row anchor mapping wrong on exception sheets                | High       | Med    | Fall back to "all images on sheet attached to all parts on sheet"; flag in `data_quality` JSONB |
| `exceljs` chokes on 241 MB despite streaming                      | Low        | High   | Pre-extract xlsx with `unzip` and read `xl/worksheets/sheetN.xml` directly via `fast-xml-parser` |
| Claude Code handoff cache generation takes >1 Claude Code session | Med        | Low    | Break into batches of 50 tasks per session; resume via cache key          |
| Ollama Qwen2-VL hallucinates CN translations                      | Med        | Med    | Cross-validate against Anthropic Batch in production; flag `data_quality.translation_confidence` |
| MinIO docker image breaks in reviewer's environment               | Low        | Med    | Document tested versions in `docker-compose.yml`; provide `setup.sh` healthcheck |
| Test data file changes (recruiter sends v2)                       | Low        | High   | Two-track structure absorbs schema drift via `rules.yaml` per dealer       |
| Reviewer doesn't read past README                                 | Med        | Med    | README has TL;DR + comparison summary + "what to look at first"            |
| Track B PoC stalls Track A delivery                               | Med        | High   | Track B scope tightly bounded (3 dbt models max); cut to comparison-only if needed |
| Schema migration mismatch between local and reviewer's Docker     | Low        | High   | `drizzle-kit migrate` runs on container start                              |

---

## 13. Open Questions for Recruiter

All filed in `docs/QUESTIONS_FOR_RECRUITER.md`. Top 5:

1. **PDF test page 1** contains 4 bullets ("Maintain content and posting calendar...") that appear to be paste-error from a marketing-role JD. Confirm: ignore?
2. **`make = "Kayo"`** — nowhere in the data; inferred from model codes. Should the pipeline hard-code this or treat as runtime config per dealer?
3. **R2 credentials** — for the submission, MinIO is used as a local R2 stand-in. Provide R2 prod creds, or is MinIO acceptable for review?
4. **Sub-assemblies** (`"1-1"`, `"1-6L"`) — separate `products` rows with parent FK, or nested children of parent row in JSONB? Current choice: separate rows (queryable). Confirm.
5. **Reference sheets** (Carburetor Jets, Spark Plugs, Owners Manuals) — ingest into `reference_specs` table or skip from primary catalog?

Assumptions proceeded on (all reversible):

- `make = "Kayo"` (inferred); pipeline accepts override via `--dealer-make` flag.
- `Sheet18` (empty) skipped silently with a logged warning.
- Variant `"EPA"` is a model property, not a SKU variant — kept in `vehicle_models.variant`.
- `OLD PART NUMBER` rows folded into `part_number_aliases`.
- `Specifications in CN` kept verbatim in `products.spec_cn`; not translated unless explicitly requested.

---

## Appendix A — Detailed Data Findings

### A.1 Sheet name patterns (110 sheets categorised)

| Pattern                                | Count | Example                                  | Schema family |
| -------------------------------------- | ----- | ---------------------------------------- | ------------- |
| `<Model> <Code>`                       | ~48   | `FOXStorm 70 AY70-2`, `Storm150 A150`    | Parts (chassis) |
| `<Model> <Code> Engine`                | ~48   | `FOXStorm 70 AY70-2 Engine`              | Parts (engine)  |
| `<Specialty>`                          | ~12   | `Carburetor Jets`, `SPARK PLUGS`         | Reference       |
| `Table of Contents`                    | 1     | `TABLE OF CONTENTS`                      | Index           |
| Junk                                   | 1     | `Sheet18`                                | Skip            |

### A.2 Header signature for parts sheets

Detected via regex on first non-empty row in each "section" within a sheet:

```
Chassis : ["No.", "Part Number", "EN name", "CN name", "Specifications in CN",
           "Qty/vehicle", "Dealer", "QTY", "Retail"]

Engine  : ["No.", "OLD PART NUMBER", "NEW PART NUMBER", "EN name", "CN name",
           "Qty/vehicle", "Dealer", "QTY", "Retail"]
```

Section header repeats 10–20 times per sheet (R15, R43, R74, R98, ...). Section title text appears 1–3 rows above the header.

### A.3 Year/make/model extraction grammar (sheet name)

```
year_pattern  = r"\((\d{4})\s*-\s*(\d{4}|\+)\)"
              | r"\((\d{4})\+\)"
              | r"\b(20\d{2})\b"

model_code    = r"(AY|AT|AU|KMB|TS|TSD|TD|TT|K2|K4|K6|KT|T2|T4|S70|S200|S350|eA|eKMB)\d*-?\d*"

variant       = r"\bEPA|EFI|D\b" appended to model_code
```

Examples processed correctly:

| Sheet name                              | year_start | year_end | model_code  | variant |
| --------------------------------------- | ---------- | -------- | ----------- | ------- |
| `PREDATOR 125 (2016-2020)`              | 2016       | 2020     | AT125-B     |         |
| `Bull 180 AU180 (2020-2022)`            | 2020       | 2022     | AU180       |         |
| `AU180-2 (2023+) Parts Diagram`         | 2023       | null     | AU180-2     |         |
| `2024+ TT125 EPA`                       | 2024       | null     | TT125       | EPA     |
| `TT125-EFI (2025+)`                     | 2025       | null     | TT125       | EFI     |

### A.4 Image-row anchor extraction

From `xl/drawings/drawingN.xml`:

```xml
<xdr:oneCellAnchor>
  <xdr:from>
    <xdr:col>0</xdr:col>
    <xdr:colOff>0</xdr:colOff>
    <xdr:row>149</xdr:row>     <!-- 0-indexed; section at xlsx row 150 -->
    <xdr:rowOff>0</xdr:rowOff>
  </xdr:from>
  <xdr:ext cx="438150" cy="314325"/>
  <xdr:pic>
    <xdr:blipFill>
      <a:blip r:embed="rId9"/>   <!-- rels lookup → image5.jpg -->
    </xdr:blipFill>
  </xdr:pic>
</xdr:oneCellAnchor>
```

Mapping algorithm:

1. Parse `drawingN.xml.rels` → `rId → image filename`.
2. Parse `drawingN.xml` → list of `(row, rId)`.
3. Look up `sheetN.xml.rels` to confirm `drawingN.xml` belongs to sheet.
4. For each image's anchor row, find the nearest section header row that follows in the same sheet — that's the section the image belongs to.

### A.5 Counts (from probe run)

```
Total sheets               : 110
TOC sheet                  : 1
Empty/junk sheet           : 1
Parts (chassis) sheets     : ~48
Parts (engine) sheets      : ~48
Reference sheets           : ~12
Total embedded images      : 1586
Distinct image SHA-256s    : TBD (run-time dedup; expected ~1400)
Estimated rows in DB       : ~12,000–18,000 distinct products (after variant collapse)
Estimated LLM calls         : ~200–400 (translate + audit), ~$3–5 with cache cold
```

---

## Appendix B — Glossary

| Term                | Definition                                                                 |
| ------------------- | -------------------------------------------------------------------------- |
| ADR                 | Architecture Decision Record. Markdown doc per major design call.          |
| Bronze/Silver/Gold  | Medallion lakehouse layers — raw / conformed / business-ready.             |
| Callout number      | The "1, 2, 3..." numbers printed on a schematic image, referenced in the `No.` column. |
| DLQ                 | Dead-letter queue — failed jobs parked for manual retry.                   |
| Fitment             | The set of `{year, make, model}` tuples a part is compatible with.         |
| Idempotent          | Re-running with the same input produces the same output, no side effects.  |
| Lineage             | Trace of data: which row in which file at which time produced which output. |
| OEM                 | Original Equipment Manufacturer (Kayo here).                               |
| Schematic           | Exploded-parts diagram image. One sheet has 10–20.                         |
| SKU variant         | Same callout, different actual part number (color, date-effective).        |
| Z-ORDER             | Delta Lake data-skipping optimisation along a chosen column.               |

---

**End of plan.** Next artifact: ADR-001 (two-track monorepo rationale), then ADR-002 (JSONB fitment), then Day 1 PM code (`xlsx-reader.ts`).
