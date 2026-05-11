# Track B Sample Output

Pre-computed artefacts from running the Track B Dagster + Iceberg pipeline against the same source xlsx as Track A. Committed so reviewers can inspect Iceberg metadata, the Dagster asset graph, and dbt run output without booting the stack.

---

## What's inside

| Path                                | Contents                                                                 |
| ----------------------------------- | ------------------------------------------------------------------------ |
| `iceberg/metadata.json`             | Iceberg table metadata for `silver_parts_atomic` (schema, snapshots)     |
| `iceberg/manifest-list.json`        | Manifest-list pointer for the latest snapshot                            |
| `dagster/asset-graph.md`            | Asset DAG with materialisation cadence and partition strategy            |
| `dagster/run-log.txt`               | Sample Dagster run-log lines for a clean materialisation                 |
| `dbt/run-results.json`              | dbt run summary (model timing, status, row counts)                       |
| `dbt/manifest.json`                 | dbt manifest excerpt — model definitions, sources, refs                  |
| `bench-results.json`                | DuckDB-on-Iceberg fitment-query latency benchmark output                 |

---

## How these were produced

```bash
cd track-b-data-engineering
docker-compose up -d minio iceberg-rest
poetry install
poetry run track-b-ingest ../shared/sample-data/example.xlsx
poetry run dbt run --project-dir dbt --profiles-dir dbt
poetry run track-b-bench --queries 500
```

The metadata/manifest JSON files are real outputs from `pyiceberg`'s table-introspection APIs against the materialised tables. Reviewers can reproduce the full dataset by running the same commands against the source xlsx.

---

## Cross-track parity

Every row in the gold Iceberg mart matches the row shape of `products` in Track A's PostgreSQL:

- Same `part_number` keying (NULLS NOT DISTINCT semantics via `MERGE INTO`)
- Same `fitment` JSON structure
- Same SHA-256 image-key contract (objects are written to MinIO `s3://catalog-images/<sha256>.png`)
- Same `data_quality` provenance JSON

This means a marketplace-sync consumer reading the Track B gold mart sees the same wire format as one reading the Track A `products` table — the lakehouse choice is invisible to downstream.
