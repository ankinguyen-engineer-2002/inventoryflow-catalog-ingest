# Dagster Asset Graph

Materialised view of the Track B asset DAG. Reflects what `dagster.materialize_all()` walks when `track-b-ingest` runs end-to-end.

---

## Asset DAG

```
                        ┌────────────────────────────────┐
                        │  source_xlsx (root)            │
                        │  resource: SourceXlsxResource  │
                        └──────────────┬─────────────────┘
                                       │
                                       ▼
                        ┌────────────────────────────────┐
                        │  bronze_catalog_rows           │
                        │  partition: dealer × file_hash │
                        │  io_mgr: iceberg               │
                        └──────────────┬─────────────────┘
                                       │
                       ┌───────────────┼───────────────┐
                       ▼                               ▼
        ┌──────────────────────────┐    ┌──────────────────────────┐
        │ silver_parts_atomic      │    │ silver_fitment_atomic    │
        │ MERGE INTO iceberg       │    │ exploded fitment rows    │
        └──────────┬───────────────┘    └───────────┬──────────────┘
                   │                                │
                   └─────────────┬──────────────────┘
                                 ▼
                  ┌──────────────────────────────────┐
                  │  gold_products_mart              │
                  │  partition: dealer × day         │
                  │  parquet zstd, sort=part_number  │
                  └──────────┬───────────────────────┘
                             │
                  ┌──────────┴───────────┐
                  ▼                      ▼
        ┌──────────────────┐   ┌──────────────────────┐
        │ gold_marketplace │   │ llm_audit_findings   │
        │ flat view        │   │ Jaccard consensus    │
        └──────────────────┘   └──────────────────────┘
```

---

## Asset properties

| Asset                     | Compute      | Partition strategy            | Materialisation cadence |
| ------------------------- | ------------ | ----------------------------- | ----------------------- |
| `bronze_catalog_rows`     | Polars       | dealer × xlsx file_hash       | on-demand (event-driven from object-store ingest) |
| `silver_parts_atomic`     | dbt-duckdb   | dealer × day(ingested_at)     | every bronze refresh (auto-materialise) |
| `silver_fitment_atomic`   | dbt-duckdb   | dealer × day(ingested_at)     | every bronze refresh (auto-materialise) |
| `gold_products_mart`      | dbt-duckdb   | dealer × day                  | hourly schedule + on-demand |
| `gold_marketplace_view`   | dbt-duckdb   | not partitioned (view)        | view-only, no materialisation |
| `llm_audit_findings`      | Python + AI  | dealer × day                  | nightly schedule        |

---

## Run cadence

- **bronze ←→ silver**: auto-materialise on upstream change (Dagster's `AutoMaterializePolicy.eager()`).
- **silver ←→ gold**: scheduled hourly via `ScheduleDefinition` `gold_refresh_hourly`.
- **llm_audit_findings**: scheduled nightly via `ScheduleDefinition` `audit_nightly`.
- **Streaming demo**: Redpanda → RisingWave → live_inventory_join. Not in the asset DAG (continuous compute, observed via Dagster sensors).
