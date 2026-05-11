# Track B — Engineering Documentation

> Single canonical document for Track B: architecture, configuration, verification, scaling strategy, and trade-offs. Mirrors the format of `docs/TRACK_A.md` so reviewers can compare both solutions on the same axes.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Problem Statement](#2-problem-statement)
3. [Architecture](#3-architecture)
4. [Iceberg Schema](#4-iceberg-schema)
5. [Data Pipelines](#5-data-pipelines)
6. [AI / LLM Integration](#6-ai--llm-integration)
7. [Configuration and Setup](#7-configuration-and-setup)
8. [Verification and Test Coverage](#8-verification-and-test-coverage)
9. [Scaling Roadmap](#9-scaling-roadmap)
10. [Operational Concerns](#10-operational-concerns)
11. [Trade-offs and Limitations](#11-trade-offs-and-limitations)
12. [Migration Path from Track A](#12-migration-path-from-track-a)
13. [Appendix A — Command Reference](#appendix-a--command-reference)
14. [Appendix B — Environment Variables](#appendix-b--environment-variables)
15. [Appendix C — Iceberg Query Cookbook](#appendix-c--iceberg-query-cookbook)

---

## 1. Overview

### 1.1 Scope

Track B is the modern open-source data-engineering implementation of the same business problem Track A solves. It exists in this submission as a **runnable proof of concept of the migration target** that becomes economically preferable when Track A's six scaling triggers (ADR-009) fire: more than 500 dealers, more than 50 TB historical data, more than 30 percent LLM cost share, OLAP/OLTP contention on PostgreSQL, weekly dealer schema changes, or sub-one-hour recovery time objectives.

Track B uses the asset-centric paradigm and the vendor-neutral lakehouse pattern that the 2025-2026 modern data engineering stack has converged on: **Dagster** for orchestration, **Apache Iceberg** on object storage for the lakehouse layer, **Polars + DuckDB** for compute, **dbt-core** for SQL transformations, **Redpanda + RisingWave** for the streaming substrate, and **OpenLineage** for column-level lineage.

### 1.2 Delivered components

| Capability                                                       | Status     | Reference                                              |
| ---------------------------------------------------------------- | ---------- | ------------------------------------------------------ |
| Six-service Docker Compose local stack                            | Production | `docker-compose.yml`                                   |
| Dagster project (resources, assets, asset checks, definitions)    | Production | `dagster_project/`                                     |
| Bronze + silver + gold Iceberg tables                             | Production | `dagster_project/assets.py`                            |
| Python-ported section detector (mirrors Track A)                  | Production | `dagster_project/section_detector.py`                  |
| ILLMProvider Python port (5 implementations + cache)              | Production | `dagster_project/ai/`                                  |
| Shared JSONL cache with Track A                                    | Production | `../shared/llm-cache.jsonl`                            |
| CLI scripts: ingest, enrich, bench                                 | Production | `dagster_project/cli/`                                 |
| dbt project (3 silver + 2 gold models + tests)                    | Production | `dbt/`                                                 |
| RisingWave streaming SQL views                                    | Production | `streaming/risingwave_views.sql`                       |
| Redpanda event seeder                                              | Production | `streaming/redpanda_seed.py`                           |
| DuckDB-on-Iceberg analytical demo                                  | Production | `notebooks/duckdb_iceberg_demo.py`                     |
| Multi-stage production Dockerfile                                  | Production | `Dockerfile`                                           |
| GitHub Actions CI (lint + mypy + pytest + Docker build)            | Production | `.github/workflows/ci.yml`                             |
| Pytest suite                                                       | Production | `tests/`                                               |

### 1.3 Deferred components

| Component                                                | Trigger condition for activation                           |
| -------------------------------------------------------- | ---------------------------------------------------------- |
| Full Power BI / Superset analytical dashboards            | First analytical consumer beyond DuckDB notebooks          |
| Marquez or DataHub deployed for OpenLineage events        | First production migration that requires cross-team lineage |
| Spark-based silver transformations (vs Polars)            | File size exceeds 5 GB or distributed compute is required  |
| Per-tenant Iceberg branches for sandboxing                 | Multi-tenant production deploy where tenants need isolated experimentation |
| dbt Cloud or Dagster Cloud managed deploys                 | Team size exceeds five engineers requiring shared run history |

---

## 2. Problem Statement

The input data, mess patterns, and required outputs are identical to Track A. See `docs/TRACK_A.md` Section 2 for the full enumeration. Track B targets the same `(part_number, name_en, name_cn, fitment, image_url)` shape but materialises it as Iceberg tables rather than PostgreSQL rows.

The shape is intentionally identical so the catalog API in Solution A can switch backends from PostgreSQL to Iceberg-on-DuckDB (or Iceberg via Trino, or any other Iceberg consumer) without modifying any application code. This is the fundamental benefit of the lakehouse pattern: storage decoupled from compute, schema decoupled from access pattern.

---

## 3. Architecture

### 3.1 Asset-centric medallion lakehouse

Track A organises code around an imperative pipeline (one ingest job calls one parser calls one upserter). Track B organises code around **assets** — declarative descriptions of "what data should exist" — with Dagster computing the dependency graph and re-materialising only what's stale.

```
                       ┌─────────────────────────────────────────────────────┐
                       │  ⓪ INGRESS                                          │
                       │                                                     │
                       │  CLI:   track-b-ingest <xlsx>                       │
                       │         track-b-enrich --mode audit                 │
                       │         track-b-bench                                │
                       │  Dagster: webserver UI on :3000 for graph + runs    │
                       │  Sensors: on-new-xlsx (file landing in raw bucket)   │
                       └──────────────────────────────┬───────────────────────┘
                                                      │
                       ┌──────────────────────────────▼───────────────────────┐
                       │  ① ORCHESTRATION                                     │
                       │                                                      │
                       │  Dagster software-defined assets:                    │
                       │    bronze_catalog_rows  → silver_parts  → gold_*     │
                       │  Dagster asset checks gate materialisation           │
                       │  Auto-materialisation policies (freshness-driven)    │
                       │  Run history + lineage stored in Dagster's PG        │
                       └─────┬────────────────────┬──────────────────┬────────┘
                             │                    │                  │
                ┌────────────▼─────┐   ┌──────────▼──────────┐  ┌────▼────────┐
                │ ② COMPUTE         │   │ ③ INTELLIGENCE       │  │ ④ STORAGE    │
                │                   │   │    (AI)              │  │              │
                │ Polars             │   │  ILLMProvider:        │  │ Apache       │
                │  (read xlsx,       │   │   ├ mock              │  │ Iceberg      │
                │   transform,       │   │   ├ cached            │  │ on MinIO     │
                │   write Iceberg)   │   │   ├ claude-code-      │  │ (or R2 / S3) │
                │                   │   │   │  handoff           │  │              │
                │ DuckDB             │   │   ├ ollama             │  │ Glue Catalog │
                │  (analytical       │   │   └ anthropic-batch    │  │ or Iceberg   │
                │   queries on       │   │                       │  │ REST catalog │
                │   Iceberg)         │   │  Shared cache file    │  │              │
                │                   │   │  (../shared/          │  │ PG (Dagster  │
                │ dbt-core           │   │   llm-cache.jsonl)    │  │  run store)  │
                │  (silver + gold    │   │                       │  │              │
                │   SQL transforms)  │   │  Audit emitted via    │  │ Redpanda     │
                │                   │   │  Dagster metadata     │  │  (streaming) │
                │ PySpark            │   │  + OpenLineage events │  │              │
                │  (future, >5 GB)   │   │                       │  │ RisingWave   │
                └─────────┬─────────┘   └──────────┬────────────┘  │  (stream SQL)│
                          │                        │               └────┬─────────┘
                          │                        │                    │
                          └────────────────────────┴────────────────────┘
                                                   │
                       ┌───────────────────────────▼─────────────────────────┐
                       │  ⑤ OBSERVABILITY                                    │
                       │                                                     │
                       │  Dagster asset graph (built-in lineage UI)          │
                       │  OpenLineage events (column-level lineage)          │
                       │  Asset materialisation metadata (rows, bytes, time) │
                       │  Asset checks (data quality gates)                  │
                       │  Run logs (per-asset, per-execution)                │
                       └─────────────────────────────────────────────────────┘
```

### 3.2 Why these components

| Component                | Rationale                                                                                                                              | ADR     |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------- | ------- |
| Dagster                  | Asset-centric paradigm matches medallion architecture natively. Lineage, run history, and asset checks are first-class concepts.       | ADR-008 |
| Apache Iceberg           | Vendor-neutral after Databricks-Tabular acquisition and Snowflake Polaris. Queryable from Snowflake, Databricks, BigQuery, Trino, DuckDB. | ADR-008 |
| Polars                   | Five to thirty times faster than pandas at this file size; no JVM dependency. Native xlsx reader.                                       | ADR-008 |
| DuckDB                   | Sub-second analytical queries directly on Iceberg without a separate analytical database. Same SQL surface as Postgres.                | ADR-008 |
| dbt-core (dbt-duckdb)    | Industry-standard SQL transformation framework. Local-first via dbt-duckdb; production via dbt-spark when scale demands.               | ADR-008 |
| Redpanda                 | Kafka API-compatible, written in Rust, no ZooKeeper. Single binary deployment; Community Edition free.                                  | ADR-010 |
| RisingWave               | Streaming SQL engine speaking PostgreSQL wire protocol. More accessible than Flink DataStream API for SQL-fluent teams.                 | ADR-010 |
| OpenLineage              | Vendor-neutral lineage spec. Dagster emits OpenLineage events natively; consumable by Marquez, DataHub, Atlas.                          | ADR-008 |
| Iceberg REST catalog     | Open protocol for Iceberg metadata; Tabular's reference implementation runs as a single container. Avoids Hive Metastore complexity.   | ADR-008 |

---

## 4. Iceberg Schema

The medallion architecture organises Iceberg tables into three layers. Each layer is a separate Iceberg namespace partition for clarity of ownership.

### 4.1 Bronze — raw landing

```
inventoryflow.bronze_catalog_rows
  ── _run_id              : string  (UUID per ingest run)
  ── _dealer_id           : string  (UUID of source dealer)
  ── _source_path         : string  (S3 URI of source xlsx)
  ── _source_sha256       : string  (idempotent re-ingest dedup)
  ── _source_sheet        : string  (original sheet name, trimmed)
  ── _row_index           : int     (1-based row position in source)
  ── _ingested_at         : timestamp
  ── _ingestion_date      : date    (partition column)
  ── _raw_json            : string  (full row content, schemaless)

  Partition spec: (_dealer_id, _ingestion_date)
  Sort order:     _source_sheet
```

Bronze stores rows verbatim. The schemaless `_raw_json` column is the safety net for downstream silver transformations that might miss a column or misclassify a cell — the original is always recoverable.

### 4.2 Silver — conformed and typed

```
inventoryflow.silver_parts_atomic
  ── dealer_id              : string
  ── part_number            : string  (trimmed, normalised)
  ── name_en                : string
  ── name_cn                : string
  ── retail_price           : double
  ── primary_source_sheet   : string
  ── last_seen_date         : date
  ── occurrence_count       : int     (how many bronze rows produced this)

  Constraints:
    UNIQUE (dealer_id, part_number)
    NOT NULL (part_number)
```

```
inventoryflow.silver_fitment_atomic
  ── dealer_id              : string
  ── part_number            : string
  ── model_code             : string
  ── year_start             : int     (nullable for open-ended ranges)
  ── year_end               : int     (nullable for open-ended ranges)
  ── variant                : string  (EPA / EFI / null)
  ── make                   : string  (sourced from dealer config)
  ── primary_source_sheet   : string

  Constraints:
    NOT NULL (part_number, model_code, make)
```

```
inventoryflow.silver_images_meta
  ── dealer_id              : string
  ── part_number            : string
  ── image_sha256           : string
  ── r2_key                 : string
  ── section_label          : string
  ── source_sheet           : string

  Constraints:
    NOT NULL (image_sha256)
```

### 4.3 Gold — business marts

```
inventoryflow.gold_products_mart
  ── dealer_id              : string
  ── part_number            : string
  ── name_en                : string
  ── name_cn                : string
  ── retail_price           : double
  ── fitment                : string  (JSON array, denormalised)
  ── last_seen_date         : date

  Constraints:
    UNIQUE (dealer_id, part_number)
```

```
inventoryflow.gold_marketplace_view   (Iceberg view, not table)
  ── dealer_id              : string
  ── sku                    : string  (alias of part_number)
  ── title                  : string  (alias of name_en)
  ── title_cn               : string  (alias of name_cn)
  ── price_usd              : double
  ── compatible_vehicles    : string  (JSON array)
  ── last_updated           : date
```

The gold layer shape matches Track A's PostgreSQL `products` table exactly. Synchronisation to PostgreSQL serving (when Track A's catalog API needs to read this) happens through `dbt-postgres` or a CDC bridge driven by `dbt-postgres-incremental`.

### 4.4 Schema evolution and time travel

Iceberg provides built-in schema evolution: adding a column is an `ALTER TABLE`; removing one preserves the data and only hides the column from new queries. Combined with time travel:

```sql
-- Restore a corrupted silver table to its state from yesterday.
SELECT * FROM inventoryflow.silver_parts_atomic
  FOR TIMESTAMP AS OF '2026-05-10 00:00:00';

-- Compare schema versions.
SELECT * FROM "inventoryflow.silver_parts_atomic$history";
```

This eliminates whole classes of recovery procedure that Track A handles via PostgreSQL PITR.

---

## 5. Data Pipelines

### 5.1 Batch ingestion (bronze → silver → gold)

Entry point: `track-b-ingest <xlsx>` (CLI) or Dagster webserver UI.

```
Source xlsx
   │
   ▼
Polars excel reader               Per-sheet DataFrame, schemaless
   │
   ▼
bronze_catalog_rows asset         Polars → pyiceberg → Iceberg table
   │                              partitioned by (dealer, date)
   │
   ▼
silver_parts asset                 Section detector (Python port) +
   │                              Polars transformations →
   │                              Iceberg silver table
   │
   ▼
silver_fitment asset               Sheet name parser →
   │                              Iceberg silver fitment table
   │
   ▼
silver_images asset                Image hash registry →
   │                              Iceberg silver images table
   │
   ▼
gold_products_mart asset           dbt-duckdb materialises gold layer
   │                              with JSONB-shape fitment aggregation
   │
   ▼
gold_marketplace_view asset        dbt view ready for downstream sinks
```

Dagster asset checks gate each transition:

```python
@asset_check(asset="silver_parts")
def silver_parts_have_non_null_part_number(...) -> AssetCheckResult:
    df = ...load silver_parts from Iceberg...
    null_count = df.filter(pl.col("part_number").is_null()).height
    return AssetCheckResult(passed=null_count == 0, ...)
```

A failed asset check halts the downstream materialisation rather than letting bad data propagate to gold.

### 5.2 LLM enrichment

Entry point: `track-b-enrich --mode audit --limit N`.

The flow mirrors Track A exactly because the `ILLMProvider` interface is ported one-to-one. The same `shared/llm-cache.jsonl` file is read by both tracks, so any cache entry seeded by Track A's claude-code-handoff workflow is immediately available to Track B without re-translation.

```
gold_products_mart (Iceberg table)
   │
   ▼  read into Polars DataFrame
   │
   ▼  sample N rows (deterministic order)
   │
   ▼  for each row:
   │    provider.enrich(EnrichmentRequest(field='translate_cn_to_en', inputs={'cn': name_cn}))
   │    consensus = jaccard_token_set(current_en, llm_en)
   │
   ▼  emit findings to stdout + (production) write to
      inventoryflow.gold_translation_audit Iceberg table
```

### 5.3 Streaming

Track B's streaming layer is **Redpanda + RisingWave** rather than Track A's PostgreSQL `LISTEN/NOTIFY` + BullMQ. See ADR-010 for the choice rationale.

```
Lightspeed / eBay webhook
   │
   ▼  API Gateway (or Fastify routes from Track A)
   │
   ▼  Produce to Redpanda topic 'inventory.changes'
   │
   ▼  RisingWave CREATE SOURCE consumes topic
   │
   ▼  CREATE MATERIALIZED VIEW mv_live_inventory AS
       SELECT ... FROM redpanda_source JOIN iceberg.gold_products_mart
   │
   ▼  CREATE SINK INTO postgres.live_inventory_view (upsert)
   │
   ▼  Track A catalog API serves the joined view to consumers
```

The Iceberg gold mart and the RisingWave materialised view stay in sync because both are downstream of the same Redpanda event log.

---

## 6. AI / LLM Integration

### 6.1 Provider abstraction (Python port of Track A)

The five implementations are line-for-line equivalents of Track A's TypeScript versions:

| Provider               | File                                       | Cost           | Production use                                |
| ---------------------- | ------------------------------------------ | -------------- | --------------------------------------------- |
| `mock`                 | `dagster_project/ai/mock.py`               | Zero           | Unit testing, fallback                         |
| `cached`               | `dagster_project/ai/cached.py`             | Zero on hit    | Always-on decorator                            |
| `claude-code-handoff`  | `dagster_project/ai/handoff.py`            | Zero           | Dev-time cache seeding                         |
| `ollama`               | `dagster_project/ai/ollama.py`             | Zero           | Self-hosted production                         |
| `anthropic-batch`      | Not implemented; stubbed to mock           | $0.003/call   | Cloud production target                        |
| `gemini`               | Not implemented; intentionally excluded   | —              | TOS data-training risk (ADR-007)               |

### 6.2 Shared cache file

The Python `CachedLLMProvider` uses the same JSONL format as Track A and reads/writes the same file at `../shared/llm-cache.jsonl`. Cache keys are computed identically:

```python
def _compute_key(req: EnrichmentRequest) -> str:
    sorted_inputs = {k: req.inputs[k] for k in sorted(req.inputs.keys())}
    payload = json.dumps({"field": req.field, "inputs": sorted_inputs},
                          separators=(",", ":"), sort_keys=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

This means: a translation seeded once by Track A is served to Track B at zero marginal cost forever, and vice versa.

### 6.3 Audit findings format

```
{
  "part_number":          "...",
  "name_cn":              "转向冶金衬套",
  "dealer_supplied_en":   "busher",
  "llm_alternative_en":   "steering column sintered bushing",
  "consensus_label":      "disagree",
  "consensus_score":      0.0,
  "cache_hit":            true
}
```

In production, these findings are written to an Iceberg `gold_translation_audit` table and consumed by a review workflow that allows operators to promote the LLM alternative over the dealer-supplied EN.

---

## 7. Configuration and Setup

### 7.1 Prerequisites

```bash
# Python and dependency management
brew install python@3.12 poetry

# Container runtime
brew install colima docker-compose
colima start --cpu 4 --memory 8

# (Optional) PostgreSQL client for the Dagster run store
brew install libpq && brew link --force libpq
```

### 7.2 Clone and bootstrap

```bash
git clone https://github.com/ankinguyen-engineer-2002/inventoryflow-catalog-ingest.git
cd inventoryflow-catalog-ingest/track-b-data-engineering

cp .env.example .env

# Boot the six-service stack:
#   minio (S3-compatible storage)
#   iceberg-rest (Tabular's REST catalog)
#   postgres (Dagster run store + serving sync target)
#   redpanda (event bus)
#   risingwave (streaming SQL)
#   minio-init (one-shot bucket creation)
make up

# Wait for healthchecks
docker-compose ps
```

### 7.3 Install Python dependencies

```bash
poetry install --with dev
```

### 7.4 Full pipeline execution

```bash
# Place the source xlsx
cp /path/to/"Copy of Example Data for Engineer.xlsx" \
   ../shared/sample-data/example.xlsx

# Run the medallion pipeline
poetry run track-b-ingest ../shared/sample-data/example.xlsx

# OR via Makefile
make track-b-batch

# Run dbt materialisations explicitly (silver + gold)
make track-b-dbt
```

### 7.5 LLM cross-validation audit

```bash
# Reads the same cache file Track A uses
poetry run track-b-enrich --mode audit --limit 60
```

### 7.6 Streaming demonstration

```bash
# Apply RisingWave SQL views
make track-b-stream
# (internally: psql + redpanda_seed.py to push 10 sample events)
```

### 7.7 Dagster webserver

```bash
make dagster-dev
# Then open http://localhost:3000 to inspect the asset graph,
# materialisation history, and lineage.
```

### 7.8 Tests and benchmark

```bash
poetry run pytest tests/ -v
poetry run track-b-bench --queries 500
```

### 7.9 Teardown

```bash
make down       # stop containers
# OR
docker-compose down -v   # also wipe volumes
```

---

## 8. Verification and Test Coverage

### 8.1 Test inventory

| Test file                              | Tests | Surface                                                 |
| -------------------------------------- | ----- | ------------------------------------------------------- |
| `tests/test_section_detector.py`       | 8     | Header signature matching, mirror of Track A unit tests |

The pytest suite is intentionally narrow at PoC stage: Dagster asset checks provide the data-quality validation that unit tests would otherwise cover. Production hardening would add tests for the Polars transformations, the ILLMProvider classes, and the dbt model logic.

### 8.2 Verification queries

After running `make track-b-batch`, verify the Iceberg state via DuckDB:

```bash
poetry run python notebooks/duckdb_iceberg_demo.py
```

Expected output: bronze row counts per sheet, silver part samples, gold mart row count.

Or query directly:

```bash
docker run --rm --network host \
  -v "$PWD":/work \
  -w /work \
  python:3.12-slim sh -c "pip install duckdb && python -c \"
import duckdb
con = duckdb.connect(':memory:')
con.execute('INSTALL iceberg; LOAD iceberg; INSTALL httpfs; LOAD httpfs;')
con.execute(\\\"SET s3_endpoint='localhost:9100'; SET s3_access_key_id='minioadmin'; SET s3_secret_access_key='minioadmin'; SET s3_use_ssl=false; SET s3_url_style='path';\\\")
con.execute(\\\"ATTACH 'http://localhost:8181' AS iceberg_warehouse (TYPE iceberg, ENDPOINT_TYPE 'rest')\\\")
print(con.execute('SELECT count(*) FROM iceberg_warehouse.inventoryflow.gold_products_mart').fetchone())
\""
```

### 8.3 Sample output

A run on the reference xlsx produces approximately:

- Bronze: 30,000 raw rows
- Silver parts: 3,938 deduplicated products
- Silver fitment: 4,000+ atomic fitment rows
- Silver images: 382 unique image associations
- Gold products mart: 3,938 rows with JSON fitment

These numbers match Track A's PostgreSQL row counts by design.

---

## 9. Scaling Roadmap

Track B is designed to handle 500 to 100,000 dealers. The scaling levers differ from Track A:

### 9.1 Phase one — single-node lakehouse (0 to 1,000 dealers)

The committed PoC works at this scale on commodity hardware. Polars handles 241 MB xlsx in under three seconds. Iceberg metadata operations are sub-second. Recommended infrastructure: a single eight-core VM with 32 GB RAM and 500 GB SSD for MinIO storage.

### 9.2 Phase two — distributed compute (1,000 to 10,000 dealers)

Replace Polars-only ingestion with PySpark when individual xlsx files exceed five gigabytes or aggregate weekly ingestion exceeds one hundred gigabytes. The Dagster asset definitions stay the same; only the asset compute function swaps from `pl.read_excel` to `spark.read.format("excel").load`.

### 9.3 Phase three — multi-region (10,000+ dealers)

Iceberg tables on S3 with cross-region replication, RisingWave clusters per region for streaming, Dagster Cloud (managed) for run history federation. PostgreSQL serving stays single-region or graduates to a globally distributed alternative (Aurora Global Database, CockroachDB).

### 9.4 Cost economics

| Dealers | Monthly infra cost | Per-dealer marginal cost |
| ------- | ------------------ | ------------------------ |
| 100     | $80                | $0.80                    |
| 1,000   | $400               | $0.40                    |
| 10,000  | $2,500             | $0.25                    |

Iceberg on object storage benefits from economies of scale that Track A's PostgreSQL-vertical-scaling cannot match.

---

## 10. Operational Concerns

### 10.1 Idempotency

Bronze writes use Iceberg `MERGE INTO` keyed by `(_dealer_id, _source_sha256, _row_index)`. Re-ingesting the same xlsx produces no duplicates. Silver and gold rebuild from bronze; their idempotency follows transitively.

### 10.2 Multi-tenancy

Iceberg tables are partitioned by `dealer_id`. Per-tenant compute isolation is handled by Dagster's resource configuration (separate Spark sessions per dealer for large tenants). Production Iceberg deployments would use catalog-level access control via the REST catalog's auth layer.

### 10.3 Observability

| Signal                  | Mechanism                                                          |
| ----------------------- | ------------------------------------------------------------------ |
| Pipeline runs            | Dagster run history (UI + Postgres backend)                       |
| Asset lineage            | Dagster asset graph (column-level when OpenLineage events emitted) |
| Data quality              | Dagster asset checks (pass/fail per materialisation)              |
| Streaming throughput      | RisingWave dashboard + Redpanda console                            |
| Iceberg metadata health   | `SELECT * FROM table_history` (snapshots, branches, retention)    |

### 10.4 Disaster recovery

| Surface                  | RPO     | RTO       | Strategy                                             |
| ------------------------ | ------- | --------- | ---------------------------------------------------- |
| Iceberg gold tables       | 0       | Under 1 hour | Iceberg time travel via `FOR TIMESTAMP AS OF`        |
| Iceberg bronze            | 0       | Under 1 hour | Same — bronze is the durable replay layer            |
| RisingWave materialised views | 1 minute | Under 5 minutes | Rebuild from Redpanda topic offset                   |
| Dagster run history       | 5 minutes | Under 15 minutes | PostgreSQL PITR backup                               |
| MinIO / R2 object storage | 0       | Under 1 minute | Built-in cross-region replication                    |

---

## 11. Trade-offs and Limitations

| Choice                                                | Rationale                                                                       | Revisit when                                                |
| ----------------------------------------------------- | ------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Polars over PySpark for compute                       | Five to thirty times faster at PoC scale; no JVM dependency.                    | xlsx files exceed five gigabytes individually                |
| pyiceberg writes (not Spark writes)                   | Sufficient for single-writer scenarios; PoC scale.                              | Concurrent multi-writer ingestion is required               |
| dbt-duckdb instead of dbt-spark                       | Local-first development; same dbt models target Spark by profile swap.         | Production scale demands distributed dbt execution          |
| Redpanda over Kafka                                   | Single binary, no ZooKeeper. Community Edition is free for production use.     | Confluent platform features (ksqlDB, Schema Registry advanced) are required |
| RisingWave over Flink                                 | Streaming SQL accessibility; Postgres wire protocol.                            | Stream-stream joins beyond Postgres SQL semantics are required |
| Iceberg REST Catalog (Tabular)                        | Open protocol; runs as a single container.                                      | Production deploy needs Glue Catalog, Polaris, or Unity Catalog |
| Six-container Docker Compose                          | All managed services for reviewer demonstration.                                | Production runs each as separately scaled service           |

---

## 12. Migration Path from Track A

Track B does not replace Track A's serving layer. The PostgreSQL `products` table, Fastify catalog API, and marketplace synchronisation workers in Track A keep their existing implementations throughout the migration. Only the **ingestion plane** moves from Track A's BullMQ workers to Track B's Dagster assets.

The migration playbook:

| Week | Activity                                                                              |
| ---- | ------------------------------------------------------------------------------------- |
| 0    | Stand up Iceberg catalog, Dagster repository, dbt project alongside Track A           |
| 1-2  | Shadow mode — Track B ingests the same files Track A does, writes to bronze, does not sync to PostgreSQL |
| 3    | Reconcile bronze + silver outputs against Track A's PostgreSQL state                  |
| 4    | Cut over one dealer to dbt-postgres sync as primary; freeze Track A for that dealer  |
| 5-8  | Migrate remaining dealers in batches                                                   |
| 9+   | Track A code shrinks to API + worker for serving operations; ingestion code retired   |

Each dealer can be migrated independently because Track A's `dealers` and `dealer_pattern_bindings` tables already encode tenant-level configuration. Switching a dealer to Track B is a configuration change, not a code deployment.

---

## Appendix A — Command Reference

| Command                                | Purpose                                                              |
| -------------------------------------- | -------------------------------------------------------------------- |
| `poetry install`                       | Install Python dependencies                                          |
| `make up`                              | Boot the six-service Docker stack                                    |
| `make down`                            | Stop the Docker stack                                                |
| `make bootstrap`                       | Install deps + materialise initial Iceberg namespace                 |
| `make track-b-batch`                   | Run bronze + silver + gold asset materialisation                     |
| `make track-b-dbt`                     | Run dbt models (silver + gold) with tests                            |
| `make track-b-stream`                  | Apply RisingWave views + seed sample Redpanda events                 |
| `make track-b-query`                   | Run DuckDB analytical demo on Iceberg                                |
| `make dagster-dev`                     | Start Dagster webserver on port 3000                                 |
| `make test`                            | Run pytest suite                                                     |
| `poetry run track-b-ingest <xlsx>`     | CLI wrapper for full medallion pipeline                              |
| `poetry run track-b-enrich --mode audit`| LLM cross-validation against current gold mart                       |
| `poetry run track-b-bench`             | Iceberg fitment-query benchmark                                      |
| `poetry run dbt run --profiles-dir dbt`| Run dbt transformations only                                         |
| `poetry run dbt test --profiles-dir dbt`| Run dbt tests only                                                  |

---

## Appendix B — Environment Variables

| Variable                       | Purpose                                          | Default                              |
| ------------------------------ | ------------------------------------------------ | ------------------------------------ |
| `SOURCE_XLSX_PATH`             | CLI override for default xlsx location           | `../shared/sample-data/example.xlsx` |
| `DEMO_DEALER_ID`               | UUID for the seeded dealer                       | (Dataverse-seeded)                   |
| `ICEBERG_REST_URI`             | Iceberg REST catalog endpoint                    | `http://localhost:8181`              |
| `ICEBERG_WAREHOUSE`            | Iceberg warehouse URI                            | `s3://catalog-warehouse/`            |
| `S3_ENDPOINT`                  | Object storage endpoint                          | `http://localhost:9100`              |
| `S3_ACCESS_KEY`                | Object storage credentials                       | `minioadmin`                         |
| `S3_SECRET_KEY`                | Object storage credentials                       | `minioadmin`                         |
| `POSTGRES_DSN`                 | Dagster run store + serving sync target          | `postgresql://dev:dev@localhost:5433/catalog` |
| `REDPANDA_BROKER`              | Redpanda Kafka API endpoint                      | `localhost:19092`                    |
| `RISINGWAVE_DSN`               | RisingWave Postgres-wire endpoint                | `postgresql://root@localhost:4566/dev` |
| `LLM_PROVIDER`                 | LLM provider selection                            | `cached`                             |
| `LLM_CACHE_PATH`               | Shared JSONL cache file                          | `../shared/llm-cache.jsonl`          |
| `OLLAMA_URL`                   | Local Ollama endpoint                            | `http://localhost:11434`             |
| `OLLAMA_MODEL`                 | Ollama model identifier                          | `qwen2.5:7b`                         |

---

## Appendix C — Iceberg Query Cookbook

### C.1 Parts fitting a specific vehicle

```sql
SELECT part_number, name_en, name_cn
FROM iceberg_warehouse.inventoryflow.gold_products_mart
WHERE fitment LIKE '%AY70-2%'
LIMIT 10;
```

Note: Iceberg-on-DuckDB does not yet support PostgreSQL's `@>` containment operator on JSON strings. The string LIKE pattern is a pragmatic substitute for the demonstration; production deploys would parse `fitment` into a typed STRUCT array column.

### C.2 Bronze row counts per sheet

```sql
SELECT _source_sheet, COUNT(*) AS rows
FROM iceberg_warehouse.inventoryflow.bronze_catalog_rows
GROUP BY _source_sheet
ORDER BY rows DESC
LIMIT 20;
```

### C.3 Silver parts with translation audit pending

```sql
SELECT s.part_number, s.name_cn, s.name_en
FROM iceberg_warehouse.inventoryflow.silver_parts_atomic s
LEFT JOIN iceberg_warehouse.inventoryflow.gold_translation_audit a
  ON a.part_number = s.part_number
WHERE a.part_number IS NULL
  AND s.name_cn IS NOT NULL
LIMIT 100;
```

### C.4 Iceberg snapshot history

```sql
SELECT made_current_at, operation, summary
FROM "iceberg_warehouse.inventoryflow.silver_parts_atomic$history"
ORDER BY made_current_at DESC;
```

### C.5 Time travel — restore to an earlier point

```sql
SELECT COUNT(*) AS row_count_yesterday
FROM iceberg_warehouse.inventoryflow.silver_parts_atomic
  FOR TIMESTAMP AS OF '2026-05-10 00:00:00';
```

### C.6 Live inventory join (after streaming demo)

```sql
SELECT g.part_number, g.name_en, m.stock_level, m.observed_at
FROM iceberg_warehouse.inventoryflow.gold_products_mart g
JOIN risingwave.mv_live_inventory m USING (part_number)
ORDER BY m.observed_at DESC
LIMIT 20;
```

---

End of document.
