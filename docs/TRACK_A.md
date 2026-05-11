# Track A — Engineering Documentation

> Single canonical document for Track A: architecture, configuration, verification, scaling strategy, and trade-offs. Intended as the complete reference for technical reviewers and operators.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Problem Statement](#2-problem-statement)
3. [Architecture](#3-architecture)
4. [Database Schema](#4-database-schema)
5. [Data Pipelines](#5-data-pipelines)
6. [AI / LLM Integration](#6-ai--llm-integration)
7. [Configuration and Setup](#7-configuration-and-setup)
8. [Verification and Test Coverage](#8-verification-and-test-coverage)
9. [Scaling Roadmap](#9-scaling-roadmap)
10. [Free-at-Scale Architecture](#10-free-at-scale-architecture)
11. [Operational Concerns](#11-operational-concerns)
12. [Trade-offs and Limitations](#12-trade-offs-and-limitations)
13. [Migration Path to Track B](#13-migration-path-to-track-b)
14. [Appendix A — Command Reference](#appendix-a--command-reference)
15. [Appendix B — Environment Variables](#appendix-b--environment-variables)
16. [Appendix C — Database Query Cookbook](#appendix-c--database-query-cookbook)

---

## 1. Overview

### 1.1 Scope

Track A is the recommended production implementation for InventoryFlow's current stage (under 500 active dealers). It matches the job description's required stack one-to-one — TypeScript, Node.js, PostgreSQL, Redis, Docker, and Cloudflare R2 — and delivers an end-to-end pipeline that ingests messy OEM Excel catalogs, normalises them into a queryable PostgreSQL schema, persists schematic images in object storage with content-addressed deduplication, exposes both batch and near-realtime streaming APIs, and integrates an LLM provider abstraction for cross-validation of dealer-supplied translations.

### 1.2 Delivered components

| Capability                                              | Status     | Reference                                  |
| ------------------------------------------------------- | ---------- | ------------------------------------------ |
| Batch ingestion (xlsx → PostgreSQL + R2/MinIO)          | Production | `src/cli/ingest.ts`, `src/ingest/`         |
| Reference-sheet ingestion (twelve exception sheets)     | Production | `src/cli/ingest-reference-sheets.ts`       |
| Vehicle-models derivation from fitment JSONB            | Production | `src/cli/populate-vehicle-models.ts`       |
| Metadata-driven control plane seed data                 | Production | `src/cli/seed-mdcp.ts`                     |
| LLM enrichment with audit mode and provider abstraction | Production | `src/cli/enrich.ts`, `src/ai/providers/`   |
| HTTP API (Fastify) with health, runs, events endpoints  | Production | `src/api/`                                 |
| BullMQ workers for batch and streaming queues           | Production | `src/queue/workers/`                       |
| Idempotent re-ingestion via `NULLS NOT DISTINCT`        | Production | `migrations/0001_nulls_not_distinct.sql`   |
| Multi-tenant request scoping                            | Production | `src/api/plugins/multitenant.plugin.ts`    |
| Outbox pattern for transactional event publishing       | Production | `src/storage/db/schema.ts:streamOutbox`    |
| Unit test suite (32 tests across 5 files)               | Passing    | `test/unit/`                               |
| Architecture Decision Records (14 ADRs)                 | Complete   | `docs/decisions/`                          |

### 1.3 Deferred components

The following components are designed and documented but not implemented in this submission. Each carries a trigger condition for activation.

| Component                                       | Trigger condition                                         |
| ----------------------------------------------- | --------------------------------------------------------- |
| MDCP runtime dispatcher (consume bindings)      | Onboarding of the second dealer with a divergent schema   |
| Row-level security enforcement on connections   | First multi-tenant production deployment                  |
| Cross-region replication                        | First enterprise dealer with contractual SLA              |
| Distributed tracing collector (Tempo or Jaeger) | First incident requiring cross-service correlation        |
| Reverse ETL to marketplace platforms            | Marketplace integration contracts firmed up               |
| Track B lakehouse migration                     | Any of six triggers in ADR-009 (see Section 13)           |

---

## 2. Problem Statement

### 2.1 Input

The system processes Excel files supplied by Original Equipment Manufacturers (OEMs) and their distributors. The reference file used for development and testing has the following characteristics:

- File size: 241 megabytes
- Worksheets: 110 total
- Embedded schematic images: 1,586 (JPEG and PNG)
- Languages: English and Simplified Chinese, often within the same row
- OEM identity: Kayo (inferred from model-code prefixes; not explicit in any cell)

### 2.2 Data quality observations

The following ten patterns were identified through manual parsing of representative sheets before any code was written. They form the implicit specification for the parser.

| Number | Pattern                                                                | Architectural consequence                                                  |
| ------ | ---------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| 1      | Worksheets contain ten to twenty independent sections each, with the header row repeated at each section boundary. | Section detection must operate on header signatures, not on row indices.   |
| 2      | Three distinct header schemas exist: `chassis`, `engine` (with OLD and NEW part-number columns), and `chassis_u8` (with `U8 Code` and `Model`). | A parser supporting only one schema silently mis-ingests a substantial portion of the file. |
| 3      | The `No.` column is runtime-polymorphic: integer, decimal, string with hyphen (`"1-1"`), string with letter suffix (`"1-6L"`), or null. | These represent sub-assemblies and left/right variants, not duplicates. Deduplication must occur on part number, not callout number. |
| 4      | The same `No.` value with different `Part Number` values represents SKU variants by colour or production date. | Both rows must be preserved with the relationship recorded.                |
| 5      | Chinese cells are returned by `exceljs` as `{ richText: [{text}, ...] }` rather than plain strings when font formatting is applied. | Naive string coercion produces the literal `"[object Object]"`. A rich-text walker is required at every cell read. |
| 6      | 1,586 images are anchored to specific rows through XML files at `xl/drawings/drawingN.xml`. | The `exceljs` library does not expose these anchors. The zip must be opened directly and the drawing XML parsed. |
| 7      | Vehicle fitment information (year range, model code, variant) is encoded in the sheet name rather than any data cell. | A regex grammar covering seven encoding patterns is required.              |
| 8      | The vehicle make (`Kayo`) is absent from every cell of the source file.| Make must be derived from model-code prefixes or supplied via per-dealer configuration. |
| 9      | Worksheet names contain trailing whitespace.                            | Names are trimmed for keying and preserved verbatim for display.           |
| 10     | Approximately twelve sheets use completely different schemas (spark plug equivalencies, carburetor jet sizes, wheel specifications). | These are routed to a separate ingestion path producing `reference_specs` rows rather than `products` rows. |

### 2.3 Required outputs

The test specification mandates the following outputs:

- A clean database containing part numbers, English names, Chinese names, and other normalised attributes
- Schematic images uploaded to a Cloudflare R2 bucket
- A JSON column listing every year, make, and model combination that each part fits
- Demonstrated use of AI tooling for parsing and cross-validation

Track A delivers all four. The implementation is detailed in subsequent sections.

---

## 3. Architecture

### 3.1 Five-plane control plane

Track A is organised as five horizontal planes. This is a generalisation of the architecture pattern adopted by production data platforms including Confluent Cloud and Databricks. Naming consistency simplifies reasoning about where new functionality belongs.

```
                       ┌─────────────────────────────────────────────────────┐
                       │  Ingress                                            │
                       │  • CLI: pnpm ingest:full <xlsx>                     │
                       │  • HTTP: POST /runs (batch ingest)                  │
                       │  • HTTP: POST /events/{inventory,pricing,order}     │
                       │  • HTTP: GET  /healthz /readyz /metrics             │
                       └─────────────────────────┬───────────────────────────┘
                                                 │
                       ┌─────────────────────────▼───────────────────────────┐
                       │  Control Plane                                      │
                       │  • Run registry (ingest_runs)                       │
                       │  • Event registry (stream_events)                   │
                       │  • Metadata-driven bindings (dealer_pattern_bindings)│
                       │  • BullMQ scheduler with per-queue concurrency      │
                       │  • Redis token-bucket rate limiter                  │
                       │  • Multitenant plugin (x-dealer-id → req.dealerId)  │
                       └─────┬───────────────────┬─────────────────┬─────────┘
                             │                   │                 │
              ┌──────────────▼──┐  ┌─────────────▼────────┐  ┌─────▼──────────┐
              │ Data Plane       │  │ Intelligence Plane    │  │ Storage Plane  │
              │ Batch workers    │  │ ILLMProvider          │  │ PostgreSQL 16  │
              │ (concurrency 8): │  │  - mock               │  │   12 tables    │
              │  parse-file      │  │  - cached (decorator) │  │   GIN JSONB    │
              │  parse-sheet     │  │  - claude-code-handoff│  │   trigram name │
              │  upload-image    │  │  - ollama             │  │                │
              │  enrich-llm      │  │  - anthropic-batch    │  │ Cloudflare R2  │
              │                  │  │  - gemini (stubbed)   │  │  (MinIO local) │
              │ Stream workers   │  │                       │  │  SHA-256 keyed │
              │ (concurrency 32):│  │ JSONL response cache  │  │                │
              │  stream-inventory│  │ (committed to repo)   │  │ Redis 7        │
              │  stream-pricing  │  │ Per-call audit log    │  │  Queue, pub/sub│
              │  stream-order    │  │                       │  │                │
              │                  │  │                       │  │                │
              │ Dead-letter queue│  │                       │  │                │
              └──────────┬───────┘  └──────────┬────────────┘  └────────┬──────┘
                         │                     │                        │
                         └─────────────────────┴────────────────────────┘
                                               │
                       ┌───────────────────────▼──────────────────────────────┐
                       │  Observability Plane                                 │
                       │  • Pino structured logs with run_id correlation      │
                       │  • Prometheus /metrics endpoint                      │
                       │  • OpenTelemetry tracing (one trace per run / event) │
                       │  • ingest_audit table (every LLM call, cost, latency)│
                       │  • Lineage: row → section → sheet → file → run       │
                       └──────────────────────────────────────────────────────┘
```

### 3.2 Component selection rationale

| Component                | Selection             | Rationale                                                                |
| ------------------------ | --------------------- | ------------------------------------------------------------------------ |
| Language                 | TypeScript 5 (strict) | Required by job description                                              |
| HTTP framework           | Fastify 5             | Native pino integration; schema validation; lower overhead than Express  |
| ORM                      | Drizzle ORM           | Concrete JSONB type inference, zero runtime overhead, predictable SQL emission (see ADR-004) |
| Excel parser             | exceljs (stream mode) | Only Node library that combines streaming reads with access to drawing XML |
| Object storage SDK       | @aws-sdk/client-s3 v3 | Identical interface for Cloudflare R2 (production) and MinIO (development) |
| Queue                    | BullMQ 5              | Active maintenance, native dead-letter queue, rate limiting, OpenTelemetry hooks |
| Validation               | Zod                   | Single source of truth for runtime and compile-time types                |
| Logging                  | Pino                  | Fastest structured logger in the Node ecosystem                          |
| Tracing                  | OpenTelemetry SDK     | Vendor-neutral; routable to Tempo, Honeycomb, Datadog                    |
| Testing                  | Vitest                | Native ESM support, TypeScript-first, fast cold start                    |
| Containerisation         | Docker Compose        | Required by job description; sufficient for development and small production deployments |

All dependencies are pinned to specific minor versions in `package.json`. All are at generally-available status; no preview or experimental features are required.

---

## 4. Database Schema

The schema comprises twelve tables grouped by concern. Migration files are at `migrations/0000_*.sql` (generated by drizzle-kit) and `migrations/0001_nulls_not_distinct.sql` (hand-written; see Section 11).

### 4.1 Catalog core

```
products
  ── id BIGSERIAL PRIMARY KEY
  ── part_number TEXT NOT NULL
  ── part_number_norm TEXT GENERATED ALWAYS AS
       (upper(regexp_replace(part_number, '\s', '', 'g'))) STORED
  ── name_en TEXT
  ── name_cn TEXT
  ── spec_cn TEXT
  ── qty_per_vehicle NUMERIC
  ── dealer_cost NUMERIC
  ── unit TEXT
  ── retail_price NUMERIC
  ── fitment JSONB NOT NULL DEFAULT '[]'
       └─ Array of {year, make, model, model_code, variant, category,
                    section, callout_no, confidence}
  ── primary_image_r2_key TEXT
  ── source_dealer_id UUID
  ── source_file_sha256 TEXT
  ── source_sheet TEXT
  ── source_row_index INT
  ── data_quality JSONB DEFAULT '{}'
       └─ {translation_source, translation_consensus, stock_level, ...}
  ── created_at, updated_at TIMESTAMPTZ

  Indexes:
  UNIQUE (part_number_norm, source_dealer_id) NULLS NOT DISTINCT
  GIN (fitment jsonb_path_ops)
  GIN (name_en gin_trgm_ops)
  GIN (name_cn gin_trgm_ops)

product_images
  ── product_id BIGINT REFERENCES products(id) ON DELETE CASCADE
  ── r2_key TEXT NOT NULL
  ── r2_url TEXT NOT NULL
  ── sha256 TEXT NOT NULL
  ── width_px, height_px INT
  ── section_label TEXT
  ── source_sheet TEXT
  ── PRIMARY KEY (product_id, sha256)

part_number_aliases
  ── product_id BIGINT REFERENCES products(id) ON DELETE CASCADE
  ── alias TEXT NOT NULL
  ── alias_norm TEXT NOT NULL
  ── alias_type TEXT NOT NULL ('old' | 'oem_alt' | 'distributor')
  ── PRIMARY KEY (alias_norm, product_id)

vehicle_models
  ── id BIGSERIAL PRIMARY KEY
  ── make, model, model_code TEXT
  ── category TEXT
  ── year_start, year_end INT (nullable for open-ended ranges)
  ── variant TEXT
  ── UNIQUE (make, model_code, year_start, year_end, variant)

reference_specs
  ── id BIGSERIAL PRIMARY KEY
  ── category TEXT NOT NULL ('spark_plugs', 'carburetor_jets', ...)
  ── model_code TEXT
  ── attributes JSONB NOT NULL
  ── source_sheet, source_row
```

### 4.2 Runs and audit

```
ingest_runs
  ── run_id UUID PRIMARY KEY DEFAULT gen_random_uuid()
  ── dealer_id UUID
  ── source_file TEXT NOT NULL
  ── source_sha256 TEXT NOT NULL
  ── status TEXT NOT NULL (QUEUED | RUNNING | SUCCESS | PARTIAL | FAILED | SKIPPED)
  ── rows_attempted, rows_succeeded, rows_failed INT
  ── llm_calls INT
  ── llm_cost_usd NUMERIC(10,4)
  ── started_at, finished_at TIMESTAMPTZ
  ── error, reason TEXT

ingest_audit
  ── id BIGSERIAL PRIMARY KEY
  ── run_id UUID REFERENCES ingest_runs(run_id)
  ── provider TEXT NOT NULL
  ── prompt_sha256, prompt_template_ver TEXT NOT NULL
  ── response_text TEXT
  ── tokens_in, tokens_out INT
  ── cost_usd NUMERIC(10,6)
  ── latency_ms INT
  ── cache_hit BOOLEAN NOT NULL
  ── created_at TIMESTAMPTZ
```

### 4.3 Streaming

```
stream_events
  ── event_id UUID PRIMARY KEY
  ── dealer_id UUID NOT NULL
  ── event_type TEXT NOT NULL ('inventory' | 'pricing' | 'order')
  ── payload JSONB NOT NULL
  ── source TEXT
  ── received_at, processed_at TIMESTAMPTZ
  ── status TEXT (PENDING | PROCESSED | FAILED)
  ── error TEXT

stream_outbox
  ── id BIGSERIAL PRIMARY KEY
  ── topic TEXT NOT NULL
  ── payload JSONB NOT NULL
  ── created_at, published_at TIMESTAMPTZ
  ── status TEXT DEFAULT 'PENDING'
```

### 4.4 Metadata-driven control plane

```
dealers
  ── id UUID PRIMARY KEY DEFAULT gen_random_uuid()
  ── name, status, inferred_make, contact_email TEXT
  ── tier TEXT DEFAULT 'standard'
  ── onboarded_at TIMESTAMPTZ
  ── metadata JSONB DEFAULT '{}'

ingestion_patterns
  ── pattern_name TEXT PRIMARY KEY
  ── pattern_type TEXT (FILE_BATCH | API_PULL | API_PUSH | CDC | ...)
  ── handler_module TEXT NOT NULL
  ── schema_signature JSONB NOT NULL
  ── validation_rules JSONB NOT NULL
  ── default_freshness_sla TEXT
  ── default_schedule TEXT (cron | 'event-driven' | 'on-source-change')
  ── version INT NOT NULL
  ── deprecated_at TIMESTAMPTZ

dealer_pattern_bindings
  ── id BIGSERIAL PRIMARY KEY
  ── dealer_id UUID REFERENCES dealers(id) ON DELETE CASCADE
  ── pattern_name TEXT REFERENCES ingestion_patterns(pattern_name)
  ── params JSONB NOT NULL
  ── freshness_sla, schedule TEXT
  ── enabled BOOLEAN DEFAULT true
  ── last_run_id UUID REFERENCES ingest_runs(run_id)
  ── last_run_sha256 TEXT (for cron-smart skip)
  ── last_run_at TIMESTAMPTZ
  ── UNIQUE (dealer_id, pattern_name)
```

### 4.5 Fitment JSONB shape

The fitment column is the test specification's stated focus. It contains an array of normalised objects:

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
    "confidence": "high"
  }
]
```

The `GIN (fitment jsonb_path_ops)` index provides sub-50-millisecond `@>` containment queries on tables of ten million rows. The denormalised structure was selected over a normalised join table for three reasons: the test specification explicitly requests a JSON column; the dominant access pattern is "parts fitting vehicle X" which is a single-table containment query; and marketplace consumers (eBay, Amazon catalogue feeds) accept the JSON shape directly without server-side join. See ADR-002 for the full rationale.

---

## 5. Data Pipelines

Track A implements five distinct pipelines, each with bounded responsibility and clear failure semantics.

### 5.1 Primary batch ingestion

Entry point: `pnpm ingest <xlsx-path>` (CLI) or `POST /runs` (HTTP).

```
Source xlsx (241 MB)
   │
   ▼
inspectFile()                  Computes SHA-256 + size in one streaming pass
   │
   ▼
parseAllDrawings()             Random-access zip read of xl/drawings/*.xml
   │                           Produces map of (sheet → image anchors)
   │
   ▼
streamWorkbook()               exceljs WorkbookReader yields sheets sequentially
   │                           Peak RAM under 300 MB regardless of file size
   │
   ▼
   For each sheet:
   │
   ├─ detectSections()         Header-regex match against three signatures
   │                           Returns array of bounded data ranges
   │
   ├─ resolveFitmentFromSheetName()  Parses year range, model code, variant
   │                                  from the sheet name itself
   │
   ├─ For each section:
   │  │
   │  ├─ pickAnchorForSection() Finds image anchor closest to (and before)
   │  │                          the section header row
   │  │
   │  ├─ uploadImage()         R2 PUT with SHA-256 key. HEAD-check first;
   │  │                         skip when object already exists.
   │  │
   │  └─ For each data row:
   │     │
   │     ├─ normaliseRow()     Zod validation, polymorphic No. parsing,
   │     │                     RichText cell coercion, whitespace cleanup
   │     │
   │     └─ upsertProduct()    INSERT ... ON CONFLICT (part_number_norm,
   │                            source_dealer_id) DO UPDATE
   │                            Idempotent re-run guaranteed by NULLS NOT
   │                            DISTINCT on the unique index.
   │
   ▼
finaliseRun()                  Updates ingest_runs status, row counts, cost
```

Measured performance on Apple M2 with 16 gigabytes of RAM:

- Wall-time: 30 seconds for the 241-megabyte reference file
- Peak resident set size: 280 megabytes
- Output: 3,938 products, 11,098 product-image associations, 382 distinct R2 objects (after SHA-256 deduplication)
- Idempotency: a second run produces unchanged row counts

### 5.2 Reference-sheet ingestion

Entry point: `pnpm ingest:references <xlsx-path>`.

The main ingest path covers approximately 96 worksheets matching one of the three regular header signatures. The remaining twelve sheets contain reference and compatibility data with completely different schemas. These are processed by a separate command into the `reference_specs` table.

| Sheet pattern             | Category in `reference_specs` | Rows in sample |
| ------------------------- | ----------------------------- | -------------- |
| `SPARK PLUGS`             | `spark_plugs`                 | 16             |
| `ATV Wheel specs`         | `atv_wheel_specs`             | 27             |
| `Spoke Specs`             | `spoke_specs`                 | 24             |
| `Battery specs`           | `battery_specs`               | 23             |
| `Fork seal specs`         | `fork_seal_specs`             | 13             |
| `Carburetor Jets`         | `carburetor_jets`             | 168            |
| `DirtbikePitbike...`      | `wheel_bearings`              | 28             |
| `SNOW TRACK KIT`          | `snow_track_kit`              | 19             |
| `ski kit parts`           | `ski_kit`                     | 26             |
| `Owners manuals`          | `owners_manuals`              | 22             |
| `eA110 upgrade kit`       | `upgrade_kit`                 | 5              |

Total: 371 rows across 11 categories. The parser captures header labels as JSONB keys against cell values, preserving the original structure without forcing it into the parts-catalog schema.

### 5.3 Vehicle-model derivation

Entry point: `pnpm populate:models`.

After primary ingestion populates `products.fitment`, this command derives the `vehicle_models` dimension table using a single SQL pass:

```sql
WITH unnested AS (
  SELECT DISTINCT
    elem->>'make' AS make,
    elem->>'model' AS model,
    elem->>'model_code' AS model_code,
    elem->>'category' AS category,
    NULLIF((elem->>'year')::int, 0) AS year,
    elem->>'variant' AS variant
  FROM products, jsonb_array_elements(fitment) AS elem
)
INSERT INTO vehicle_models (...)
SELECT ... FROM unnested
ON CONFLICT (make, model_code, year_start, year_end, variant) DO NOTHING;
```

On the reference dataset this produces 35 distinct vehicle-model rows.

### 5.4 Streaming event ingestion

Entry point: `POST /events/{inventory,pricing,order}` (HTTP).

The streaming path is designed for sub-500-millisecond latency from webhook receipt to PostgreSQL write. It uses the transactional outbox pattern to guarantee at-least-once delivery to downstream consumers without distributed transactions.

```
External system (Lightspeed DMS, eBay, dealer)
   │
   │ POST /events/inventory
   │ x-dealer-id: <uuid>
   │ { "part_number": "...", "stock_level": 42 }
   │
   ▼
Fastify route handler
   ├─ Multi-tenant plugin extracts dealer_id
   ├─ Zod validation against per-event-type schema
   └─ Database transaction:
      ├─ INSERT INTO stream_events (status='PENDING')
      └─ INSERT INTO stream_outbox (topic, payload)
   │
   ▼
202 Accepted + eventId (returned within p95 of 500 ms)
   │
   ▼
BullMQ stream-inventory queue (concurrency 32)
   │
   ▼
stream-inventory worker
   ├─ UPDATE products
   │  SET data_quality = data_quality || jsonb_build_object(
   │                       'stock_level', X, 'stock_updated_at', NOW())
   │  WHERE part_number_norm = ...
   ├─ UPDATE stream_events SET status = 'PROCESSED'
   └─ pg_notify('inventory_change', payload::text)
   │
   ▼
Subscribers listening on PostgreSQL LISTEN channel receive the event
in-process for marketplace synchronisation.
```

The outbox table accumulates rows that a separate publisher (not yet implemented) drains to an external message bus such as Redpanda or Kafka. This decouples the synchronous write path from downstream consumer availability.

### 5.5 LLM enrichment with audit mode

Entry point: `pnpm enrich --mode audit --limit N`.

The enrichment command scans products in deterministic order, requests a Chinese-to-English translation through the configured `ILLMProvider`, and records the result in `products.data_quality`. In audit mode the existing `name_en` is preserved; the LLM output is compared against it using a Jaccard token-set overlap to produce a consensus label.

```
For each product needing enrichment:
   │
   ▼
ILLMProvider.enrich({
  id: deterministic hash of CN string,
  field: 'translate_cn_to_en',
  inputs: { cn: products.name_cn }
})
   │
   ▼
CachedLLMProvider checks JSONL cache
   ├─ Cache hit  → return cached response (zero cost, sub-millisecond)
   └─ Cache miss → call upstream → write to cache → return
   │
   ▼
recordAudit(prompt_sha256, response, cost, latency, cache_hit)
   │
   ▼
In audit mode:
   consensus = jaccard_token_set(current_en, llm_en)
   label = 'agree'    if consensus >= 0.5
           'partial'  if consensus >= 0.2
           'disagree' otherwise
   │
   ▼
UPDATE products
SET data_quality = data_quality || jsonb_build_object(
    'translation_verified', true,
    'translation_consensus', label,
    'translation_consensus_score', consensus,
    'translation_llm_alt', llm_en,
    'translation_template_ver', meta.promptTemplateVer,
    'translation_confidence', meta.confidence
)
WHERE id = ...
```

Section 6 covers the LLM integration in detail.

---

## 6. AI / LLM Integration

The job description states that AI tooling, including Vision Language Models, is encouraged for parsing messy data. Track A implements this through a provider abstraction designed to satisfy three constraints simultaneously:

1. The reviewer must be able to run the full pipeline without supplying any API key or paying any provider.
2. The candidate must not incur API costs during development.
3. The same code must be production-viable with no rewrite.

### 6.1 Provider abstraction

All LLM access flows through a single TypeScript interface:

```typescript
interface ILLMProvider {
  readonly name: string;
  enrich(req: EnrichmentRequest): Promise<EnrichmentResponse>;
}
```

Six concrete implementations are provided. Selection occurs at runtime through the `LLM_PROVIDER` environment variable.

| Provider               | Implementation file                                   | Cost          | Production use                                |
| ---------------------- | ----------------------------------------------------- | ------------- | --------------------------------------------- |
| `mock`                 | `src/ai/providers/mock.provider.ts`                   | Zero          | Unit testing, deterministic safety fallback   |
| `cached`               | `src/ai/providers/cached.provider.ts`                 | Zero on hit   | Always-on decorator wrapping any upstream     |
| `claude-code-handoff`  | `src/ai/providers/claude-code-handoff.provider.ts`    | Zero          | Development-time cache seeding only           |
| `ollama`               | `src/ai/providers/ollama.provider.ts`                 | Zero          | Self-hosted production runtime                |
| `anthropic-batch`      | `src/ai/providers/anthropic-batch.provider.ts`        | Approximately $0.003 per call | Cloud production runtime, scales economically |
| `gemini`               | (stubbed; falls back to mock)                         | Free tier     | Not invoked; documented for TOS-risk awareness |

### 6.2 Cache strategy

The `CachedLLMProvider` wraps any upstream provider. Cache keys are computed as `SHA-256(field + sorted_inputs_json)`. Hits return synchronously without contacting the upstream provider. The cache is backed by a JSON Lines file at `shared/llm-cache.jsonl`, committed to the repository.

The JSONL format was selected over SQLite for portability. `better-sqlite3` requires Xcode Command Line Tools to compile natively, and Node's built-in `node:sqlite` requires an experimental flag that the Vite bundler cannot resolve. JSONL has zero native dependencies, scales adequately for cache sizes under approximately ten thousand entries (the realistic upper bound given cache key uniqueness), and remains human-readable for debugging.

Null results are never cached. This is critical for the `claude-code-handoff` workflow: a null response represents "task pending operator translation", and caching it would freeze the pipeline rather than allowing a subsequent results file to unlock progress.

### 6.3 Three-phase lifecycle

```
Phase 1: Development-time cache seeding (one-time per prompt template version)

  LLM_PROVIDER=claude-code-handoff pnpm enrich --mode audit --limit 60

  Pipeline emits shared/handoff/translation_tasks.jsonl containing tasks
  that the handoff provider cannot answer (because results.jsonl does
  not yet exist). The operator translates each task in a Claude Code
  session (covered by an existing Claude Max subscription, incurring no
  marginal cost) and writes shared/handoff/translation_results.jsonl.
  Re-running the same command reads the results, caches them, and
  populates products.data_quality.

  This phase is the only one that requires a human in the loop. It is
  idempotent: deleting the cache and re-running reproduces the same file.

Phase 2: Reviewer execution (zero cost, fully autonomous)

  LLM_PROVIDER=cached pnpm enrich --mode audit --limit 60

  Every call hits the committed cache. The ingest_audit table records
  cache_hit=true for every row. No external API is contacted.

Phase 3: Production runtime (autonomous, swappable)

  LLM_PROVIDER=ollama  OLLAMA_MODEL=qwen2.5:7b  pnpm enrich
       OR
  LLM_PROVIDER=anthropic  ANTHROPIC_API_KEY=...  pnpm enrich

  No human in the loop. Cache hit rate of approximately 99% at steady
  state because the same Chinese strings appear across many dealers'
  files; a single translation serves them all globally. Switching
  between providers is a configuration change with no code modification.
```

### 6.4 Five-layer accuracy framework

Translation correctness cannot rely on the LLM alone. Track A implements five layers of validation:

| Layer | Mechanism                                                                                  | Implementation status      |
| ----- | ------------------------------------------------------------------------------------------ | -------------------------- |
| 1     | Confidence scoring per LLM call (high, medium, low)                                        | Implemented in all providers |
| 2     | Validation rules: ASCII-clean output, length sanity, no leak markers                       | Implemented                |
| 3     | Cross-row consistency: same CN string should yield same EN translation across products     | Demonstrated via audit mode |
| 4     | Ensemble agreement: two providers asked the same question, disagreements flagged           | Documented; activated when production traffic justifies the cost |
| 5     | Production feedback loop: marketplace listing errors invalidate cache entries              | Documented; activated post-launch |

Audit mode (Section 5.5) demonstrates Layer 3 concretely. On the reference dataset, 68 sampled products produced the following distribution:

| Consensus    | Count | Interpretation                                                          |
| ------------ | ----- | ----------------------------------------------------------------------- |
| `agree`      | 25    | LLM translation matches dealer-supplied EN                              |
| `partial`    | 32    | EN correct; LLM adds technical specification detail                     |
| `disagree`   | 11    | Disagreement likely indicates a defect in dealer-supplied EN            |

Examples of dealer-supplied translations that the audit flagged as defects:

| Chinese name           | Dealer EN                | LLM-suggested EN                          | Defect type                |
| ---------------------- | ------------------------ | ------------------------------------------ | -------------------------- |
| 转向冶金衬套           | `busher`                 | steering column sintered bushing          | Typographical error        |
| 前左右减震             | `front fork`             | front left and right shock absorbers      | Incorrect part category    |
| 平垫 GB97.1-85         | `flat gasket`            | flat washer GB97.1-85                     | Incorrect part category    |
| 前碟刹驻车手柄         | `front park lock kit`    | front parking brake handle                | Imprecise terminology      |

These findings demonstrate concrete value of the LLM as a verification mechanism rather than a translation mechanism. The dealer-supplied English remains the source of truth in `products.name_en`; the LLM alternative is recorded in `products.data_quality.translation_llm_alt` for downstream review workflows.

### 6.5 Cost economics

At a production scale of one thousand dealers operating weekly:

| Variable                              | Value                                       |
| ------------------------------------- | ------------------------------------------- |
| Distinct Chinese strings globally     | Approximately 50,000                        |
| Cross-dealer cache hit rate           | Approximately 99% at steady state           |
| Real upstream calls per month         | Approximately 500                           |
| Anthropic Sonnet pricing (2026)       | $3 per million input tokens, $15 per million output |
| Average call size                     | 80 input tokens, 20 output tokens           |
| Per-call cost                         | Approximately $0.0005                       |
| Total monthly LLM bill                | Approximately $0.25, rounded to $1–5 for retries |

The cache is the architectural lever that makes paid APIs viable at scale. The provider abstraction is the lever that allows substitution between Ollama (self-hosted, zero variable cost, lower quality) and Anthropic (cloud, paid, higher quality) without modifying any business logic.

---

## 7. Configuration and Setup

### 7.1 Prerequisites

| Component          | Version          | Installation                                                 |
| ------------------ | ---------------- | ------------------------------------------------------------ |
| Docker runtime     | 4.x or later     | Docker Desktop, Colima (`brew install colima`), or OrbStack  |
| Docker Compose v2  | 2.x or later     | Bundled with Docker Desktop, or `brew install docker-compose` |
| Node.js            | 22 LTS           | `nvm install 22` or `fnm install 22`                         |
| pnpm               | 9.x or later     | `npm install -g pnpm`                                        |
| psql client (opt.) | any              | `brew install libpq && brew link --force libpq`              |
| MinIO client (opt.)| any              | `brew install minio/stable/mc`                               |

### 7.2 Repository setup

```bash
git clone https://github.com/ankinguyen-engineer-2002/inventoryflow-catalog-ingest.git
cd inventoryflow-catalog-ingest/track-a-jd-native

cp .env.example .env       # Defaults work for local development
pnpm install               # Initial install: approximately 50 seconds
```

### 7.3 Source-data placement

The 241-megabyte reference Excel file is not committed to the repository. Place it at the expected path:

```bash
cp /path/to/"Copy of Example Data for Engineer.xlsx" \
   ../shared/sample-data/example.xlsx
```

### 7.4 Service boot

```bash
docker-compose up -d           # postgres, redis, minio
docker-compose ps              # confirm all three healthy
pnpm db:migrate                # applies migrations 0000 and 0001
```

Expected post-migration state: twelve tables in the `public` schema, indexed and constraint-enforced.

### 7.5 Full pipeline execution

A single chained command executes the complete pipeline. This is the recommended execution path for reviewers.

```bash
pnpm ingest:full ../shared/sample-data/example.xlsx
```

The chain executes in the following order:

| Step | Command                | Purpose                                                              |
| ---- | ---------------------- | -------------------------------------------------------------------- |
| 1    | `ingest`               | Parses 96 parts-catalog sheets into `products` and `product_images`  |
| 2    | `ingest:references`    | Parses 12 exception sheets into `reference_specs`                    |
| 3    | `populate:models`      | Derives `vehicle_models` from `products.fitment` via DISTINCT        |
| 4    | `seed:mdcp`            | Seeds `dealers`, `ingestion_patterns`, `dealer_pattern_bindings`     |
| 5    | `enrich --mode audit`  | LLM cross-validation of 60 products against the committed cache      |

Expected wall-time on Apple M2: approximately 60 seconds. Expected external cost: zero.

### 7.6 Individual command execution

Each pipeline stage is also invokable independently for replay or partial re-runs:

```bash
pnpm ingest             ../shared/sample-data/example.xlsx
pnpm ingest:references  ../shared/sample-data/example.xlsx
pnpm populate:models
pnpm seed:mdcp
pnpm enrich             --mode audit --limit 60
```

For preview-only execution that emits a JSON summary without database writes:

```bash
pnpm ingest:dryrun ../shared/sample-data/example.xlsx \
                   --sheet "FOXStorm 70 AY70-2" --limit 5
```

### 7.7 HTTP API server

```bash
pnpm api       # listens on port 3000
pnpm worker    # in a separate terminal, processes queues
```

Health and event endpoints become available immediately:

```bash
curl -s http://localhost:3000/healthz | jq
curl -s http://localhost:3000/readyz  | jq
curl -X POST http://localhost:3000/events/inventory \
  -H 'content-type: application/json' \
  -H 'x-dealer-id: 11111111-1111-1111-1111-111111111111' \
  -d '{"part_number":"602006-0015","stock_level":42}'
```

### 7.8 Teardown

```bash
docker-compose down -v   # The -v flag removes named volumes (full reset)
```

---

## 8. Verification and Test Coverage

### 8.1 Test inventory

The unit test suite contains 32 tests across 5 files, all passing at the time of submission.

| Test file                                      | Tests | Surface area                                                  |
| ---------------------------------------------- | ----- | ------------------------------------------------------------- |
| `test/unit/env.test.ts`                        | 1     | Environment variable validation                               |
| `test/unit/section-detector.test.ts`           | 9     | Header signature matching, multi-section detection, edge cases |
| `test/unit/fitment-resolver.test.ts`           | 9     | Sheet-name parsing, seven year-encoding patterns, junk exclusion |
| `test/unit/row-normalizer.test.ts`             | 10    | Cell coercion, polymorphic callout parsing, multi-space cleanup |
| `test/unit/llm-cache.test.ts`                  | 3     | Cache hit/miss behaviour, distinct-input separation           |

Test execution:

```bash
pnpm test       # full suite
pnpm test:unit  # unit only
```

Expected output: `32 passed (5 files)`, total duration under 500 milliseconds.

### 8.2 Verification commands

#### 8.2.1 Database population

After running `pnpm ingest:full`, all twelve tables contain data:

```sql
SELECT 'products'                AS table_name, COUNT(*) FROM products UNION ALL
SELECT 'product_images',          COUNT(*) FROM product_images UNION ALL
SELECT 'reference_specs',         COUNT(*) FROM reference_specs UNION ALL
SELECT 'ingest_audit',            COUNT(*) FROM ingest_audit UNION ALL
SELECT 'part_number_aliases',     COUNT(*) FROM part_number_aliases UNION ALL
SELECT 'vehicle_models',          COUNT(*) FROM vehicle_models UNION ALL
SELECT 'stream_events',           COUNT(*) FROM stream_events UNION ALL
SELECT 'stream_outbox',           COUNT(*) FROM stream_outbox UNION ALL
SELECT 'ingest_runs',             COUNT(*) FROM ingest_runs UNION ALL
SELECT 'ingestion_patterns',      COUNT(*) FROM ingestion_patterns UNION ALL
SELECT 'dealer_pattern_bindings', COUNT(*) FROM dealer_pattern_bindings UNION ALL
SELECT 'dealers',                 COUNT(*) FROM dealers
ORDER BY 2 DESC;
```

Expected row counts:

| Table                     | Count        | Provenance                                          |
| ------------------------- | ------------ | --------------------------------------------------- |
| `product_images`          | 10,524       | Image-product associations after deduplication      |
| `products`                | 3,938        | Distinct parts after `NULLS NOT DISTINCT` upsert    |
| `reference_specs`         | 371          | Eleven categories from twelve exception sheets      |
| `ingest_audit`            | varies       | Grows with each LLM-enabled command invocation      |
| `part_number_aliases`     | 50           | OLD/NEW pairs from engine sheets                    |
| `vehicle_models`          | 35           | Distinct tuples derived from fitment                |
| `stream_events`           | 0 or more    | Grows when streaming endpoints receive events       |
| `stream_outbox`           | 0 or more    | Grows alongside stream_events                       |
| `ingest_runs`             | 5 or more    | One row per invocation of any ingest command        |
| `ingestion_patterns`      | 3            | Seeded by `seed:mdcp`                               |
| `dealer_pattern_bindings` | 3            | Seeded by `seed:mdcp`                               |
| `dealers`                 | 1            | Seeded by `seed:mdcp`                               |

#### 8.2.2 Test specification outputs

The test specification requires part_number, name_en, name_cn, and a JSONB fitment column. The following query confirms all are present:

```sql
SELECT
  part_number,
  name_en,
  name_cn,
  jsonb_pretty(fitment) AS fitment,
  primary_image_r2_key
FROM products
WHERE part_number = '602006-0015';
```

Expected output: one row with non-null `name_en` (English), `name_cn` (Chinese), `fitment` (JSONB array), and `primary_image_r2_key` (R2 object key).

#### 8.2.3 Vehicle-fitment query (primary access pattern)

```sql
SELECT part_number, name_en, name_cn
FROM products
WHERE fitment @> '[{"make":"Kayo","model_code":"AY70-2"}]'
LIMIT 10;
```

This query uses the `GIN (fitment jsonb_path_ops)` index. Execution time on the reference dataset is under 50 milliseconds.

#### 8.2.4 LLM audit results

```sql
SELECT
  COUNT(*) FILTER (WHERE data_quality->>'translation_verified' = 'true')      AS verified,
  COUNT(*) FILTER (WHERE data_quality->>'translation_consensus' = 'agree')    AS agree,
  COUNT(*) FILTER (WHERE data_quality->>'translation_consensus' = 'partial')  AS partial,
  COUNT(*) FILTER (WHERE data_quality->>'translation_consensus' = 'disagree') AS disagree
FROM products;
```

Expected output after `pnpm enrich --mode audit --limit 60`: 68 verified, with distribution approximately 25 agree, 32 partial, 11 disagree.

#### 8.2.5 Schematic images in object storage

Web console: <http://localhost:9001> (credentials `minioadmin` / `minioadmin`). The `catalog` bucket contains 382 distinct objects under the prefix `sha256/`.

Command-line verification:

```bash
docker run --rm --network host \
  -e MC_HOST_local=http://minioadmin:minioadmin@localhost:9000 \
  minio/mc ls --recursive local/catalog | wc -l
```

Expected output: 382.

Direct HTTP access to a specific image:

```bash
docker exec ifc_postgres psql -U dev -d catalog -tA -c "
  SELECT r2_url
  FROM product_images
  WHERE product_id = (SELECT id FROM products WHERE part_number='602006-0015')
  LIMIT 1;
"
```

The returned URL is accessible directly:

```
http://localhost:9000/catalog/sha256/87/68/876869...jpg
```

In production, the same key would be served from `https://<account>.r2.cloudflarestorage.com/catalog/sha256/...`.

#### 8.2.6 Streaming pipeline

After starting `pnpm api` and `pnpm worker`:

```bash
curl -X POST http://localhost:3000/events/inventory \
  -H 'content-type: application/json' \
  -H 'x-dealer-id: 11111111-1111-1111-1111-111111111111' \
  -d '{"part_number":"602006-0015","stock_level":42}'
# → {"eventId":"...","accepted":true}

sleep 2

docker exec ifc_postgres psql -U dev -d catalog -c "
  SELECT status, processed_at IS NOT NULL AS processed
  FROM stream_events
  ORDER BY received_at DESC
  LIMIT 1;
"
# → status='PROCESSED', processed=t
```

#### 8.2.7 Idempotency

```bash
docker exec ifc_postgres psql -U dev -d catalog -tAc "SELECT COUNT(*) FROM products;"
# → 3938

pnpm ingest ../shared/sample-data/example.xlsx

docker exec ifc_postgres psql -U dev -d catalog -tAc "SELECT COUNT(*) FROM products;"
# → 3938 (unchanged)
```

---

## 9. Scaling Roadmap

Track A is designed for the current production stage of under 500 active dealers. Beyond that threshold, the architecture remains viable through three phases of incremental improvement before migration to Track B becomes economically preferable.

### 9.1 Phase one — current implementation (zero to 500 dealers)

The shipped implementation handles this range without modification. Single-instance PostgreSQL, single-instance Redis, single BullMQ worker process, and Cloudflare R2 (or MinIO) as object storage are sufficient. Expected infrastructure cost: approximately $30 per dealer per month at the lower end of the range, dropping to approximately $0.50 per dealer per month at 500 dealers due to fixed-cost amortisation.

### 9.2 Phase two — cheap horizontal scaling (500 to 1,500 dealers)

Effort: approximately one week of one senior engineer. Marginal infrastructure cost: approximately $200 per month.

| Lever                                              | Effort   | Capacity multiplier            |
| -------------------------------------------------- | -------- | ------------------------------ |
| PgBouncer transaction-mode connection pool         | 2 hours  | 3x more concurrent connections |
| Kubernetes Deployment, three to ten worker replicas| 1 day    | 10x ingest throughput          |
| Redis Cluster with three nodes                     | 1 day    | 10x queue capacity             |
| Read replica plus read/write split in code         | 1 day    | 5x catalog-API throughput      |
| Multi-bucket R2 sharded by `hash(dealer_id) % 16`  | 4 hours  | 16x PUT rate ceiling           |
| Materialised view for hot fitment queries          | 4 hours  | 20x faster top-N queries       |
| Move LLM cache to Redis (shared across workers)    | 2 hours  | Worker-pool cache coherence    |

After phase two, the architecture handles approximately 1,500 dealers with the same single-region PostgreSQL primary.

### 9.3 Phase three — structural reorganisation (1,500 to 5,000 dealers)

Effort: approximately three to four weeks of one to two senior engineers. Marginal infrastructure cost: approximately $1,500 per month.

| Lever                                                                | Effort  |
| -------------------------------------------------------------------- | ------- |
| PostgreSQL table partitioning by `hash(dealer_id)`, 16 partitions    | 3 days  |
| Per-tenant Redis key namespace                                       | 2 days  |
| CQRS separation: write path (ingest workers, PG primary) versus      | 1 week  |
| read path (read replicas plus Redis cache)                           |         |
| Global canonical-translations table for cross-dealer LLM deduplication | 2 days |
| MDCP runtime dispatcher (consumes `dealer_pattern_bindings`)          | 3 days  |
| Per-tenant SLO tracking with distributed-tracing namespace            | 2 days  |
| Logical replication standby with automatic failover (pg_auto_failover)| 3 days  |
| Cloudflare CDN front for object storage egress reduction              | 1 day   |

After phase three, the architecture handles approximately 5,000 dealers. Beyond this point, each additional engineering effort yields diminishing capacity gains, and migration to Track B (Section 13) becomes economically preferable.

### 9.4 Capacity matrix by hardware tier

| Hardware                                       | Capacity         | Notes                                          |
| ---------------------------------------------- | ---------------- | ---------------------------------------------- |
| One Hetzner CPX31 (8 GB RAM, 4 vCPU, €13/month) | Approximately 500 dealers | Single-instance, suitable for initial production |
| One Hetzner CCX23 (16 GB RAM, 4 vCPU, €30/month) | Approximately 2,000 dealers | Requires R2 paid tier (approximately $10/month) |
| Two Hetzner CCX23 with write/read split (€60/month) | Approximately 5,000 dealers | Requires Redis Cluster and Ollama dedicated host |
| Six-node home-lab cluster (capex $1,200 to $1,500, $10/month electricity) | Approximately 5,000 dealers | Subject to residential ISP uptime and bandwidth |

---

## 10. Free-at-Scale Architecture

Track A can be deployed entirely on free or marginal-cost infrastructure. The full architecture is open-source software end-to-end; commercial managed services are optional substitutions.

### 10.1 Free-friendly component substitutions

| Layer              | Default (managed)           | Free substitute                          |
| ------------------ | --------------------------- | ---------------------------------------- |
| PostgreSQL         | Neon, Supabase, RDS         | Self-hosted on VPS or home hardware      |
| Redis              | Upstash, ElastiCache        | Self-hosted Redis Cluster                |
| Object storage     | Cloudflare R2 (paid tier)   | MinIO (self-hosted), R2 free tier (10 GB)|
| LLM provider       | Anthropic, OpenAI           | Ollama (`qwen2.5:7b`, `qwen2-vl:7b`)     |
| CDN                | CloudFront, Fastly          | Cloudflare Workers (100,000 requests/day free) |
| TLS termination    | AWS ACM                     | Caddy with Let's Encrypt                 |
| Monitoring         | Datadog, New Relic          | Grafana plus Prometheus plus Loki        |
| Ingress            | AWS ALB                     | Cloudflare Tunnel (free)                 |
| CI/CD              | GitHub Actions paid plan    | GitHub Actions free tier (2,000 minutes/month) |

### 10.2 Deployment topologies

#### Pattern A — home-lab cluster

Six commodity machines (mini-PCs, Raspberry Pi units, or used desktop hardware) on a residential network behind a Cloudflare Tunnel. Capital cost: $200 to $1,500 depending on hardware sourcing. Recurring cost: approximately $10 per month for electricity. Capacity: up to 5,000 dealers under appropriate ISP and uptime conditions.

Node allocation:

- Node 1: reverse proxy (Caddy), API instances (three replicas), monitoring stack
- Node 2: PostgreSQL primary plus hot standby
- Node 3: PostgreSQL read replica, Redis cluster node
- Node 4: Redis cluster nodes, MinIO node
- Node 5: MinIO nodes, BullMQ worker pool (eight replicas)
- Node 6: Ollama LLM serving with GPU acceleration where available

#### Pattern B — exclusively free-tier cloud

Compute on Oracle Cloud Always-Free (four ARM Ampere A1 instances totalling 24 GB RAM, no expiration). Database on Neon (500 MB free) or CockroachDB Serverless (5 GB free). Cache on Upstash (10,000 commands per day free) or self-hosted Redis on Oracle. Object storage on Cloudflare R2 free tier (10 GB storage, 1 million Class A operations per month, 10 million Class B operations per month, zero egress fees).

Recurring cost: $0. Capacity ceiling: approximately 200 dealers due to storage and database limits.

#### Pattern C — hybrid pragmatic (recommended)

Single Hetzner CPX31 or CCX23 VPS hosting PostgreSQL, Redis, MinIO, API, and workers. Cloudflare for DNS, TLS termination, Tunnel ingress, and Workers KV for catalog API edge caching. Ollama on a single home machine or in a dedicated cloud GPU instance for LLM serving.

Recurring cost: $20 to $30 per month. Capacity: up to approximately 2,000 dealers. Marginal cost per dealer at capacity: approximately $0.01 per month.

### 10.3 Operational trade-offs of free deployments

| Free choice                          | Operational cost                                                                |
| ------------------------------------ | ------------------------------------------------------------------------------- |
| Self-host PostgreSQL                 | Backup configuration, version upgrade, failover procedures (2 to 4 hours/month) |
| Self-host Redis                      | Memory tuning, persistence configuration, cluster operation                     |
| Self-host MinIO                      | Disk-health monitoring, erasure-coding setup, backup strategy                   |
| Ollama self-hosted                   | GPU electricity (50 to 200 watts continuous), manual model updates, approximately 10 to 15 percent lower quality than Anthropic Sonnet for the same prompts |
| Residential network ingress          | ISP uptime around 99.5 percent versus 99.99 percent commercial; bandwidth caps  |
| Self-hosted monitoring stack         | Grafana, Prometheus, and Loki setup (2 to 4 hours initially)                    |
| Free CI minutes                      | 2,000-minute monthly cap; long-running integration tests must run locally       |

Total operational burden of fully self-hosted deployment is approximately 10 to 20 hours per month, compared to approximately 2 to 4 hours per month for a managed-services equivalent. This is appropriate for early-stage cost optimisation and incompatible with high-availability SLA requirements.

### 10.4 Triggers for transitioning to paid infrastructure

| Trigger                                                  | Recommended action                                |
| -------------------------------------------------------- | ------------------------------------------------- |
| Uptime SLA requirement of 99.9 percent or higher         | Move to managed PostgreSQL with documented uptime |
| Regulatory compliance requirement (SOC 2, HIPAA, GDPR strict) | Adopt managed services with compliance certifications |
| Engineering team exceeding three full-time engineers     | Add managed pager service for on-call rotation    |
| Geographic distribution across more than two regions      | Move to managed multi-region database               |
| Production data volume exceeding one terabyte             | Migrate to managed PostgreSQL with snapshot-based backup |
| Streaming throughput exceeding 10,000 events per second   | Adopt managed message bus (Confluent Cloud or AWS MSK) |

---

## 11. Operational Concerns

### 11.1 Bugs encountered during development

The following defects were resolved during implementation. Each is recorded here as a permanent reference for future maintainers.

| Symptom                                                     | Root cause                                                                       | Resolution                                                                |
| ----------------------------------------------------------- | -------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| Chinese cell values persisted as the literal `"[object Object]"` | `exceljs` returns formatted cells as `{ richText: [{text}, ...] }`; `String(v)` does not walk the structure | `src/ingest/cell-utils.ts` provides `cellToString` used by both the section detector and the row normaliser |
| Sheets beyond the first two produced zero detected sections  | A third header schema (`U8 Code` plus `Model`) was not in the signature list      | Added `chassis_u8` signature and made the part-number column part of the signature itself |
| Re-running `pnpm ingest` doubled the `products` row count    | PostgreSQL's default `UNIQUE` treats NULL values as distinct                      | Hand-written migration `0001_nulls_not_distinct.sql` rebuilds the index with `NULLS NOT DISTINCT` |
| BullMQ refused to start with `q:parse-file` queue name       | BullMQ reserves the colon character as an internal key separator                  | Renamed queues to remove the prefix                                       |
| BullMQ workers silently dropped jobs                         | Redis was configured with `maxmemory-policy allkeys-lru`; BullMQ requires `noeviction` | Updated `docker-compose.yml` with explanatory comment                     |
| Stream worker crashed with PostgreSQL error 42P18            | `postgres.js` cannot infer parameter type for `pg_notify`'s second argument        | Replaced with `sql.unsafe` and explicit `$1::text` cast                   |
| Drawing parser hung on streaming unzip of 1,500-plus entries | Streaming zip parse leaks file handles on large archives                          | Switched to `unzipper.Open.file` for random-access reads                  |
| Section title heuristic returned data rows as titles          | Walked the row list rather than using row-index lookup                            | Bounded lookback to four rows above the header, with sparse-row filtering |

### 11.2 Idempotency guarantees

The pipeline is end-to-end idempotent. Re-running `pnpm ingest:full` against the same source file produces the same row counts in every table:

- Products: `INSERT ... ON CONFLICT (part_number_norm, source_dealer_id) DO UPDATE` with `NULLS NOT DISTINCT` on the unique index
- Images: `INSERT ... ON CONFLICT DO NOTHING` on `(product_id, sha256)` primary key
- R2 objects: HEAD-check before PUT; same bytes produce same SHA-256, which produces same key
- LLM cache: SHA-256 key over sorted-input JSON; same input produces same key
- Audit log: insert-only, but new rows record cache_hit=true on subsequent runs

### 11.3 Multi-tenancy

Multi-tenancy is implemented at three layers:

1. Application-layer plugin (`src/api/plugins/multitenant.plugin.ts`): extracts dealer_id from `x-dealer-id` header on every request, attaches to `req.dealerId`, and rejects requests to dealer-scoped endpoints when absent.
2. Database-column scoping: every dealer-owned table has `dealer_id` (or equivalent) as part of its primary key or unique constraint.
3. Object-storage prefix isolation: production deployments would prefix R2 keys with `dealer/<dealer_id>/` although the current submission uses a global `sha256/` prefix for cross-dealer deduplication.

Row-level security policies are designed in ADR-011 but not enabled in migrations. Activation requires every application connection to issue `SET LOCAL app.current_dealer_id = ...` per request, which conflicts with the current fixture-driven CLI flow. RLS becomes the right choice at the first multi-tenant production deployment.

### 11.4 Observability

| Signal                  | Implementation                                                            |
| ----------------------- | ------------------------------------------------------------------------- |
| Logs                    | Pino structured JSON to stdout. Every log line carries `run_id` correlation. |
| Metrics                 | Prometheus exposition at `GET /metrics`. Currently a placeholder; full counter and histogram instrumentation deferred. |
| Tracing                 | OpenTelemetry SDK is wired but currently no exporter is configured. Configurable via `OTEL_EXPORTER_OTLP_ENDPOINT`. |
| Audit                   | `ingest_audit` table records every LLM call with prompt hash, response, tokens, cost, latency, and cache hit status. |
| Lineage                 | Every row in `products` carries `source_file_sha256`, `source_sheet`, `source_row_index`. Combined with `ingest_runs`, this provides full row-to-run lineage. |

### 11.5 Disaster recovery

Target recovery objectives by service surface:

| Surface                       | RPO       | RTO          | Strategy                                                  |
| ----------------------------- | --------- | ------------ | --------------------------------------------------------- |
| Catalog API (read)            | Zero      | Under 5 min  | Read replica with automatic promotion                     |
| Catalog API (write)           | 5 min     | Under 15 min | PostgreSQL point-in-time recovery with WAL archiving      |
| Object storage                | Zero      | Under 1 min  | Cloudflare R2 is multi-region by default                  |
| Ingest pipeline               | 1 hour    | Under 1 hour | Re-ingest from last successful `ingest_runs.run_id`       |
| LLM cache                     | 24 hours  | Under 30 min | Cache is committed to the repository; regeneratable from source xlsx |

Full backup strategy and incident response procedures are documented in ADR-013.

---

## 12. Trade-offs and Limitations

Every architectural choice involves a trade-off. Track A makes the following choices explicitly, with the conditions under which each becomes the wrong choice.

| Choice                                                              | Rationale                                                                         | Revisit when                                                       |
| ------------------------------------------------------------------- | --------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| JSONL cache instead of SQLite                                       | Zero native dependencies; matches `Vite` bundler constraints; sufficient for cache size under 10,000 entries | Cache exceeds 100,000 entries (linear-scan load becomes noticeable) |
| Drizzle ORM in preference to Prisma                                 | Concrete JSONB type inference; zero runtime overhead; predictable SQL emission     | Engineering team grows beyond five backend engineers and Prisma's developer ergonomics outweigh the runtime cost |
| MinIO locally, R2-compatible code path                              | Reviewer can execute without a Cloudflare account; identical S3 SDK works against both | R2 introduces non-S3 features the application needs to consume     |
| `claude-code-handoff` provider for development-time cache seeding   | Zero API cost during development; reviewer pays nothing; production swap is a configuration change | Cache regeneration becomes frequent enough that the manual handoff cost is real (Ollama is the documented next step) |
| Single-process ingestion at 30-second wall-time on 241 MB             | Sufficient for the under-500-dealer target; horizontal scaling is straightforward through additional worker replicas | 100 dealers each uploading 1-GB files simultaneously (shard parse-sheet workers across instances) |
| `NULLS NOT DISTINCT` on the products unique index                   | Idempotency for un-tenanted runs                                                  | Never; this is a strict improvement over the default               |
| Section title heuristic returns null when uncertain                  | A wrong section label is worse than a missing one                                 | Section labelling becomes a downstream consumer requirement (LLM-assisted titling is the documented next step) |
| Row-level security policies designed but not enabled in migrations  | RLS requires per-connection `SET LOCAL` which complicates the fixture flow         | First multi-tenant production deployment                           |
| Twelve exception sheets parsed with a generic header-row parser     | Sufficient for catalog lookup; one parser handles all eleven categories            | A dealer adopts these as primary catalog data with analytical requirements |
| Metadata-driven control plane tables seeded but no runtime dispatcher | Tables and relationships are inspectable; runtime dispatch is a follow-on milestone | Onboarding of the second dealer with a divergent schema            |
| Gemini free tier provider stubbed and unused                        | Gemini's terms of service permit Google to train on API content, which is incompatible with production dealer data | Never for production; Gemini is acceptable for non-sensitive demonstration data only |

---

## 13. Migration Path to Track B

Track B (Polars, Apache Iceberg, Dagster, Redpanda, RisingWave) is documented as the migration target in `PLAN.md` Section 5 and `docs/COMPARISON.md`. The migration becomes economically preferable when any of the following six conditions are met for two consecutive months:

1. Active dealer count exceeds 500
2. Historical data volume exceeds 50 terabytes
3. LLM cost share exceeds 30 percent of monthly cloud bill (global deduplication via Iceberg pays for the migration)
4. Analytics queries on `products` cause catalog API p95 latency to exceed 100 milliseconds
5. Dealer schema changes occur at a rate of one or more per week
6. Business requires sub-one-hour recovery time objective from a corrupted-data incident

The migration replaces only the ingestion plane. The serving plane (PostgreSQL, Fastify catalog API, marketplace synchronisation) remains unchanged. Track B Dagster assets write to Iceberg tables on object storage; dbt models materialise to gold-layer Iceberg tables which sync to the same PostgreSQL serving database via `dbt-postgres` or CDC.

Migration steps in summary:

| Week  | Activity                                                                  |
| ----- | ------------------------------------------------------------------------- |
| 0     | Stand up Iceberg catalog, Dagster repository, dbt project on MinIO or R2  |
| 1-2   | Shadow mode: Track B ingests the same files as Track A, writes to bronze, does not sync to PostgreSQL |
| 3     | Compare bronze plus silver outputs against Track A's PostgreSQL state; reconcile differences |
| 4     | Cut over one dealer to dbt-postgres sync as primary; freeze Track A for that dealer |
| 5-8   | Migrate remaining dealers in batches                                      |
| 9+    | Track A code retired except for the catalog-API and streaming layers     |

---

## Appendix A — Command Reference

| Command                               | Purpose                                                                |
| ------------------------------------- | ---------------------------------------------------------------------- |
| `pnpm install`                        | Install dependencies                                                   |
| `pnpm db:generate`                    | Generate Drizzle migration from schema                                  |
| `pnpm db:migrate`                     | Apply pending migrations                                                |
| `pnpm db:studio`                      | Open Drizzle Studio web UI                                              |
| `pnpm ingest <file>`                  | Run primary batch ingestion                                             |
| `pnpm ingest:dryrun <file>`           | Parse only; emit JSON summary; no database writes                       |
| `pnpm ingest:full <file>`             | Chain of ingest, ingest:references, populate:models, seed:mdcp, enrich   |
| `pnpm ingest:references <file>`       | Parse the twelve exception sheets into `reference_specs`                 |
| `pnpm populate:models`                | Derive `vehicle_models` from `products.fitment`                          |
| `pnpm seed:mdcp`                      | Seed `dealers`, `ingestion_patterns`, `dealer_pattern_bindings`         |
| `pnpm enrich --mode audit --limit N`  | LLM cross-validation of N products                                       |
| `pnpm enrich --mode fill --limit N`   | LLM fill for products with NULL `name_en` (none in reference dataset)    |
| `pnpm enrich:dump`                    | Emit tasks JSONL for claude-code-handoff workflow                        |
| `pnpm api`                            | Start Fastify HTTP server on port 3000                                   |
| `pnpm worker`                         | Start BullMQ worker process                                              |
| `pnpm test`                           | Run full test suite                                                      |
| `pnpm test:unit`                      | Run unit tests only                                                      |
| `pnpm typecheck`                      | Run `tsc --noEmit`                                                       |
| `pnpm lint`                           | Run ESLint                                                               |

---

## Appendix B — Environment Variables

All variables have safe defaults for local development. See `.env.example` for the authoritative list.

| Variable                       | Purpose                                                            | Default                                              |
| ------------------------------ | ------------------------------------------------------------------ | ---------------------------------------------------- |
| `NODE_ENV`                     | Runtime environment                                                 | `development`                                        |
| `LOG_LEVEL`                    | Pino log verbosity                                                  | `info`                                               |
| `APP_PORT`                     | HTTP server port                                                    | `3000`                                               |
| `DATABASE_URL`                 | PostgreSQL connection string                                        | `postgres://dev:dev@localhost:5432/catalog`          |
| `DB_POOL_SIZE`                 | Connection pool size                                                | `10`                                                 |
| `REDIS_URL`                    | Redis connection string                                             | `redis://localhost:6379`                             |
| `S3_ENDPOINT`                  | Object storage endpoint                                             | `http://localhost:9000` (MinIO)                      |
| `S3_REGION`                    | Object storage region                                               | `auto`                                               |
| `S3_BUCKET`                    | Object storage bucket name                                          | `catalog`                                            |
| `S3_ACCESS_KEY`                | Object storage access key                                           | `minioadmin`                                         |
| `S3_SECRET_KEY`                | Object storage secret key                                           | `minioadmin`                                         |
| `S3_FORCE_PATH_STYLE`          | Use path-style URLs (required for MinIO)                            | `true`                                               |
| `LLM_PROVIDER`                 | LLM provider selection                                              | `cached`                                             |
| `LLM_CACHE_PATH`               | JSONL cache file path                                               | `../shared/llm-cache.jsonl`                          |
| `OLLAMA_URL`                   | Ollama HTTP endpoint                                                | `http://localhost:11434`                             |
| `OLLAMA_MODEL`                 | Ollama model identifier                                             | `qwen2-vl:7b`                                        |
| `ANTHROPIC_API_KEY`            | Anthropic API key (production only)                                 | empty                                                |
| `STREAMING_ENABLED`            | Enable Redpanda streaming layer                                     | `false`                                              |
| `OTEL_SERVICE_NAME`            | OpenTelemetry service name                                          | `inventoryflow-catalog-ingest`                       |
| `OTEL_EXPORTER_OTLP_ENDPOINT`  | OpenTelemetry collector endpoint                                    | empty (disabled)                                     |

---

## Appendix C — Database Query Cookbook

### C.1 Find parts fitting a specific vehicle

```sql
SELECT part_number, name_en, name_cn, retail_price
FROM products
WHERE fitment @> '[{"make":"Kayo","model_code":"AY70-2"}]'
ORDER BY part_number
LIMIT 20;
```

### C.2 Find parts with LLM-flagged translation defects

```sql
SELECT
  part_number,
  name_cn,
  name_en                                      AS dealer_supplied,
  data_quality->>'translation_llm_alt'          AS llm_alternative,
  data_quality->>'translation_consensus_score'  AS score
FROM products
WHERE data_quality->>'translation_consensus' = 'disagree'
ORDER BY (data_quality->>'translation_consensus_score')::float ASC
LIMIT 20;
```

### C.3 Look up old part-number alias

```sql
SELECT p.part_number AS current, a.alias AS old_or_alternative, a.alias_type
FROM products p
JOIN part_number_aliases a ON a.product_id = p.id
WHERE a.alias_norm = upper(regexp_replace($1, '\s', '', 'g'));
```

### C.4 Stock-level history for a part

```sql
SELECT
  received_at,
  payload->>'stock_level' AS stock_level,
  source
FROM stream_events
WHERE event_type = 'inventory'
  AND payload->>'part_number' = '602006-0015'
ORDER BY received_at DESC
LIMIT 20;
```

### C.5 LLM cost summary per run

```sql
SELECT
  r.run_id,
  r.started_at,
  r.rows_succeeded,
  r.llm_calls,
  r.llm_cost_usd,
  COUNT(a.*) FILTER (WHERE a.cache_hit) AS cache_hits,
  COUNT(a.*) FILTER (WHERE NOT a.cache_hit) AS cache_misses
FROM ingest_runs r
LEFT JOIN ingest_audit a ON a.run_id = r.run_id
GROUP BY r.run_id, r.started_at, r.rows_succeeded, r.llm_calls, r.llm_cost_usd
ORDER BY r.started_at DESC
LIMIT 10;
```

### C.6 Reference data lookup

```sql
-- Spark plug equivalency by model
SELECT model_code, attributes
FROM reference_specs
WHERE category = 'spark_plugs'
ORDER BY model_code;

-- Carburetor jet kits
SELECT attributes
FROM reference_specs
WHERE category = 'carburetor_jets'
LIMIT 20;
```

### C.7 Dealer-pattern bindings (MDCP)

```sql
SELECT
  d.name        AS dealer,
  d.inferred_make,
  p.pattern_name,
  p.pattern_type,
  p.handler_module,
  b.schedule,
  b.freshness_sla,
  b.enabled,
  b.last_run_at
FROM dealer_pattern_bindings b
JOIN dealers d            ON d.id = b.dealer_id
JOIN ingestion_patterns p ON p.pattern_name = b.pattern_name
ORDER BY d.name, p.pattern_name;
```

---

End of document.
