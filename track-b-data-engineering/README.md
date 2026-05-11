# Track B — Modern OSS DE (Dagster + Iceberg + Redpanda + RisingWave)

> 2026-trendy proof-of-concept of the migration target when InventoryFlow scales past ~500 dealers / ~50 TB historical. **Not the submission recommendation** — that's Track A.

**Stack**: Python 3.12 · **Dagster** (asset-centric orchestrator) · **Apache Iceberg** (lakehouse) · **Polars** + **DuckDB** (compute) · **dbt-core** (transforms) · **Redpanda** (Kafka-API event bus) · **RisingWave** (streaming SQL) · Debezium (CDC) · OpenLineage · MinIO · Docker

See [`../PLAN.md §5`](../PLAN.md#5-track-b--modern-oss-de-dagster--iceberg--redpanda--risingwave) for the full architecture and rationale.

---

## Why this stack, not the v1 stack

The original Track B used Prefect + Delta Lake + Great Expectations. After re-evaluating against 2025–2026 modern DE trends (and per [ADR-008](../docs/decisions/ADR-008-medallion-iceberg-dagster.md)), the switches:

| v1 (old)                  | v2 (current)                                              | Why                                                            |
| ------------------------- | --------------------------------------------------------- | -------------------------------------------------------------- |
| Prefect 2.x               | **Dagster 1.x**                                           | Asset-centric matches medallion; lineage + DQ native           |
| Delta Lake                | **Apache Iceberg**                                        | Vendor-neutral (post-Tabular acquisition); 2026 trend signal   |
| Great Expectations        | **Dagster asset checks**                                  | Built-in; one tool instead of two                              |
| OpenLineage (bolt-on)     | **Dagster asset graph (native)** + OpenLineage emit       | Column-level lineage native                                    |
| (no streaming)            | **Redpanda + RisingWave**                                 | Hybrid batch + near-realtime; needed for marketplace sync      |

---

## Why this exists at all

If InventoryFlow hits ~500 dealers, Track A's single-PostgreSQL write path becomes the bottleneck and re-ingestion costs explode. Track B demonstrates the migration target: a vendor-neutral lakehouse + streaming hybrid that delivers Databricks-grade scale economics without the lock-in. PostgreSQL stays as the serving layer; only ingestion moves.

[`../docs/decisions/ADR-009-when-to-switch-tracks.md`](../docs/decisions/ADR-009-when-to-switch-tracks.md) documents the six trigger conditions.

---

## Scope of the PoC

| In scope                                                       | Out of scope                                              |
| -------------------------------------------------------------- | --------------------------------------------------------- |
| Dagster repo with 6 assets + 4 asset checks + 2 sensors        | Production-grade Dagster Cloud / serverless deploy        |
| Polars → Iceberg bronze write (single-writer, pyiceberg)       | Concurrent multi-writer Iceberg (defer to Spark+Iceberg)  |
| 3 dbt silver + 2 dbt gold models on Iceberg via DuckDB         | Full marts coverage                                       |
| 1 Redpanda topic + 1 RisingWave materialized view + sink       | Multi-topic streaming graph                               |
| 1 Great-Expectations–equivalent suite as Dagster asset checks  | Full DQ test coverage                                     |
| OpenLineage events to stdout                                   | Marquez/DataHub UI deploy                                 |
| Image upload (reuses Track A R2 module)                        | Reimplementing R2 client                                  |
| LLM calls (Python port of `ILLMProvider`)                      | Independent provider stack                                |
| Sample DuckDB notebook querying Iceberg gold                   | Production BI dashboard                                   |
| Single-tenant PoC                                              | Multi-tenant runtime (covered in [ADR-011](../docs/decisions/ADR-011-multi-tenant-isolation.md)) |

---

## Quick reference

```
dagster_project/             ← Dagster code location (entry: definitions.py)
├── assets/
│   ├── bronze.py            ← Polars + pyiceberg writes
│   ├── silver.py            ← Polars transforms + asset checks
│   └── gold.py              ← dbt run via @dbt_assets
├── asset_checks.py          ← schema_match, no_null_part, fitment_well_formed, price_in_range
├── sensors.py               ← on_new_xlsx + on_dealer_config_change
├── resources.py             ← IcebergIO, Redpanda, MinIO, dbt resources
└── definitions.py           ← Definitions(assets, asset_checks, sensors, resources)

dbt/
├── models/
│   ├── bronze/              ← source declarations (read Iceberg via DuckDB)
│   ├── silver/              ← conformed (parts_atomic, fitment_atomic, images_meta)
│   └── gold/                ← marts (products_mart, catalog_marketplace_view)
├── tests/                   ← dbt tests
├── macros/
└── seeds/

streaming/
├── risingwave_views.sql     ← CREATE SOURCE + CREATE MATERIALIZED VIEW + SINK INTO
├── redpanda_seed.py         ← sample event publisher
└── connect/
    └── debezium-postgres.json   ← CDC connector config

notebooks/
└── duckdb_demo.ipynb        ← analytics queries on Iceberg gold

tests/                       ← pytest
scripts/                     ← bootstrap, teardown
docker-compose.yml           ← pg + minio + dagster + redpanda + risingwave + kafka-connect
pyproject.toml
dbt_project.yml
```

---

## Run locally

```bash
cp .env.example .env
docker compose up -d           # pg + minio + dagster + redpanda + risingwave + connect
poetry install
make bootstrap                 # MinIO buckets + Iceberg catalog + dbt deps + Redpanda topics
make track-b-batch             # batch path: xlsx → bronze → silver → gold
make track-b-stream            # streaming path: seed events → MV → Iceberg sink
make track-b-query             # sample DuckDB query on Iceberg gold
```

**Expected** on M2 Mac, 241 MB input → batch wall-time **~2–3 min** (Polars wins read); streaming SLA **<1 s** webhook → live view.

> [!TIP]
> Open Dagster UI at `http://localhost:3000` to see the asset graph and lineage visualizations. This is the killer feature of asset-centric orchestration — every materialization shows upstream/downstream dependencies.

---

## Design choices unique to Track B v2

| Choice                                                | Why                                                                                                                                                                |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Dagster over Prefect / Airflow**                    | Asset-centric paradigm matches medallion natively; lineage + DQ built in; UI is best-in-class. ADR-008.                                                            |
| **Apache Iceberg over Delta Lake**                    | Vendor-neutral (works across Snowflake, Databricks, AWS, GCP); 2026 trend. pyiceberg writes acceptable for PoC scale.                                              |
| **Polars over pandas**                                | 5–30× faster on the file size; zero JVM; modern API.                                                                                                                |
| **dbt-duckdb on Iceberg over dbt-spark**              | Local-first; same dbt models can target Spark in prod by config swap.                                                                                              |
| **Redpanda over Kafka**                               | Single Rust binary; no ZooKeeper; Kafka API-compatible; Community Edition free.                                                                                    |
| **RisingWave over Flink**                             | Streaming SQL (no DataStream API); Postgres-wire; single binary; lighter operationally for PoC.                                                                    |
| **OpenLineage as Dagster emit, not bolt-on**          | Dagster has first-class OL integration; column-level lineage flows automatically.                                                                                  |
| **Dagster asset checks replace Great Expectations**   | One-tool problem; failures gate materialization atomically.                                                                                                        |
| **Iceberg partitioning by (dealer_id, ingestion_date)** | Multi-tenant isolation + query partition pruning. ADR-011.                                                                                                       |

---

## When this becomes the right answer (and Track A is retired)

See [ADR-009](../docs/decisions/ADR-009-when-to-switch-tracks.md). Summary triggers:

1. **>500 dealers** active weekly, OR
2. **>50 TB** historical data, OR
3. **LLM cost > 30%** of monthly cloud bill (Track B's global dedupe pays for itself), OR
4. **Analytics queries** on `products` cause >100 ms p95 latency spikes on catalog API, OR
5. **≥1 dealer schema change per week** (manual Drizzle migrations become bottleneck), OR
6. **Sub-1-hour RTO** required (Iceberg time travel is the only credible answer).

Until any of those, **Track A is correct**.

---

## What's NOT in Track B

- HTTP API surface — Track A owns serving; Track B is ingestion-only.
- Full marketplace sync — schema designed to feed it, integration is post-onboarding.
- Multi-region replication — single-region PoC; addressed in [ADR-013](../docs/decisions/ADR-013-dr-bcp-rpo-rto.md).
- Schema registry service — Iceberg catalog *is* the registry; no Confluent SR needed at this scale.
- Production-grade DataHub deploy — OpenLineage emit to stdout for PoC.
