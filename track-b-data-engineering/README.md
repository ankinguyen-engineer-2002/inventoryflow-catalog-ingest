# Track B — OSS Big-Data DE (Polars + Delta + dbt)

> Proof-of-concept of the migration target when InventoryFlow scales past ~500 dealers / ~50 TB historical. **Not the submission recommendation** — that's Track A.

**Stack**: Python 3.12 · Polars · DuckDB · Delta Lake (delta-rs) · dbt-core (dbt-duckdb + dbt-postgres) · Prefect 2.x · Great Expectations · OpenLineage · MinIO · Docker

See [`../PLAN.md §5`](../PLAN.md#5-track-b--oss-big-data-de-polars--delta-lake-medallion) for the full architecture.

---

## Why this exists

If InventoryFlow hits ~500 dealers, Track A's single-PostgreSQL write path becomes the bottleneck and re-ingestion costs explode. Track B demonstrates the migration target: a vendor-neutral lakehouse pattern that delivers the scale economics of Databricks without the lock-in. PostgreSQL stays as the serving layer; only ingestion moves.

[`../docs/decisions/ADR-009-when-to-switch-tracks.md`](../docs/decisions/ADR-009-when-to-switch-tracks.md) documents the exact trigger conditions.

---

## Scope of the PoC

This is a **proof-of-concept** to validate that the migration path is real, not a marketing slide. It does NOT replace Track A.

| In scope                                              | Out of scope                              |
| ----------------------------------------------------- | ----------------------------------------- |
| 1 Prefect flow ingesting xlsx → bronze Delta on MinIO | Production-grade orchestration cluster   |
| 3 dbt silver models                                    | Full silver coverage of every table       |
| 2 dbt gold marts                                       | Marketplace search index                  |
| 1 Great Expectations suite                            | Full DQ coverage                          |
| 1 sample DuckDB query                                 | Performance benchmark against Track A    |
| OpenLineage events to stdout                          | Marquez UI deploy                         |
| Image upload (reuses Track A R2 module)               | Reimplementing R2 client                  |
| LLM calls (reuses `ILLMProvider` ported from Track A) | Independent provider stack                |

---

## Quick reference

```
pipelines/         Polars ingestion (Python) per medallion layer
├── bronze/        xlsx → Delta raw
├── silver/        bronze → conformed (Polars + delta-rs MERGE)
└── gold/          dbt models materialised in Delta

dbt/
├── models/
│   ├── bronze/    source declarations (read-only)
│   ├── silver/    parts_atomic, fitment_atomic, images_meta
│   └── gold/      products_mart, catalog_marketplace_view
├── tests/         dbt + Great Expectations
├── macros/
└── seeds/

orchestration/    Prefect flows
notebooks/        Exploratory + DuckDB demos
tests/            pytest
```

## Run locally

```bash
cp .env.example .env
docker compose up -d           # postgres + minio + prefect-orion
poetry install
make bootstrap                 # creates buckets, dbt deps
make track-b-run               # full medallion run on sample xlsx
make track-b-query             # sample DuckDB queries demo
```

Expected wall-time on M2 Mac, 241 MB input: **~2–3 min** (Polars beats Node here on read speed).

## Status

🚧 **Scaffolded only.** Implementation lands per [`../PLAN.md §11`](../PLAN.md#11-delivery-timeline--milestones) Day 3 PM.

## Design choices unique to Track B

- **Polars over pandas** — 5–30× faster at this file size, zero JVM.
- **delta-rs over PySpark** — Rust core, no JVM dependency, perfect for files <5 GB. Spark documented as upgrade path when needed.
- **dbt-duckdb over dbt-spark** — local-first; same dbt models can target Spark in prod by config swap.
- **Prefect 2 over Airflow** — better local DX, DAG-as-code, lighter footprint.
- **OpenLineage over proprietary lineage** — vendor-neutral spec; works with Marquez, DataHub, Atlan, etc.
- **Great Expectations at silver entry** — fail fast on schema drift; alerts go through OpenLineage event stream.

## When this becomes the right answer

See ADR-009. Summary triggers:

1. >500 dealers, OR
2. >50 TB historical data, OR
3. LLM cost > 30% of monthly cloud bill (global dedupe via Delta pays for itself), OR
4. Analytics queries on the catalog start blocking the serving Postgres.

Until any of those, **Track A is correct.**
