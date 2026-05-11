# ADR-008: Medallion architecture for Track B (lakehouse PoC)

## Status
Accepted — 2026-05-11

## Context

Track B exists to demonstrate the migration path when InventoryFlow scales past Track A's economic limits (~500 dealers / 50 TB historical). Two patterns are credible:

- **A. Medallion (Bronze / Silver / Gold)** — Databricks-coined, now standard across lakehouses. Three layers of progressive cleaning.
- **B. Kappa / Lambda** — stream-first, replay-from-log. Strong for real-time but heavy for batch-dominant workloads.
- **C. Direct-to-warehouse (e.g., Snowflake, BigQuery)** — fastest delivery but vendor lock-in + cost-at-scale concerns.

Catalog ingestion is **batch-dominant** (dealers send files weekly, not events). Vendor neutrality is a hard requirement (no Databricks/Snowflake spend allowed in an early-stage startup posture).

## Decision

Adopt **medallion on OSS Delta Lake** with the following layer responsibilities:

```
BRONZE  →  Raw fidelity. 1 row per Excel data row. _raw_json column preserves
           every cell, even ones we don't currently use. Schemaless safety net.
           Partition: (_dealer_id, _ingestion_date). Z-ORDER: _source_sheet.

SILVER  →  Conformed. Typed columns. Joined dimensions (vehicle_models).
           Schema enforced. Great Expectations gates entry. SCD-2 friendly
           (effective_from/to columns).
           Tables: parts_atomic, fitment_atomic, images_meta, dealers, vehicle_models.

GOLD    →  Business marts. dbt-materialised. Documented via dbt docs.
           Tables: products_mart (denormalised, JSONB fitment), catalog_marketplace_view,
                   dealer_inventory_snapshot.
           Synced to PostgreSQL for serving (CDC via dlt or direct dbt-postgres).
```

Stack:

- Storage format: **Delta Lake** (delta-rs Python binding, no JVM).
- Compute (PoC scale): **Polars** for bronze ingestion, **DuckDB** for silver/gold queries.
- Compute (production scale, >5 GB files): **PySpark 3.5** (documented but not used in PoC).
- Transformation: **dbt-core** (dbt-duckdb for local, dbt-spark for prod when needed).
- Orchestration: **Prefect 2.x**.
- Data quality: **Great Expectations** at silver entry.
- Lineage: **OpenLineage** spec, events to stdout in PoC.

## AI suggestion vs my override

**Claude initially suggested** Kappa architecture with Kafka + Flink "for real-time freshness."

**I overrode** because:

1. **Workload reality**: dealers upload files weekly, not events. Real-time is irrelevant.
2. **Operational cost**: Kafka + Flink + Schema Registry + ZooKeeper is a 4-system commitment. Overkill for batch-dominant ingestion.
3. **Talent market**: Kafka/Flink ops talent is rarer and pricier than dbt/Spark talent. Bad bet for early-stage.
4. **Replay semantics**: Delta time-travel + Prefect retry covers everything Kappa replay does, with simpler ops.
5. **Real-time can be bolted on later**: a CDC stream from PostgreSQL to a Kafka topic for downstream consumers is a one-week project. Building stream-first today optimises for a future that may not happen.

I also rejected:

- **Iceberg over Delta**: PyIceberg write paths still pre-1.0 as of 2026-Q1; delta-rs is mature.
- **Spark for sample-scale work**: Polars on a laptop processes 241 MB faster than booting a Spark session. Spark documented as the upgrade for >5 GB.
- **Hive Metastore / Unity Catalog**: cataloging adds operational burden without value at PoC scale; Delta metadata is sufficient.

## Trade-offs accepted

- **Three-layer model adds latency** vs writing direct to gold. Mitigated: bronze→silver is sub-minute at this scale; silver→gold via dbt is sub-minute.
- **dbt + Prefect adds two toolchains** to learn. Both have excellent docs and very large communities. Reviewer cost is real if they're unfamiliar.
- **Local-first PoC**: doesn't prove cloud-deploy. Acceptable for PoC scope; mentioned in `track-b/README.md`.
- **No Marquez UI deploy** for lineage in PoC — OpenLineage events go to stdout only. Production would deploy Marquez or DataHub.

## When to revisit (i.e., when Track A → Track B is justified)

See ADR-009 for the exact criteria.

## Sources

- Databricks "Medallion architecture" canonical guide: https://docs.databricks.com/aws/en/lakehouse/medallion (retrieved 2026-05-11).
- Delta Lake 3.x release notes: https://delta.io/blog (retrieved 2026-05-11).
- delta-rs Python bindings: https://delta-io.github.io/delta-rs/ (retrieved 2026-05-11).
- Polars vs pandas benchmark, pola.rs/posts (retrieved 2026-05-11) — 5–30× speedup on file ingestion.
- OpenLineage spec: https://openlineage.io/docs (retrieved 2026-05-11).
- Kleppmann, *Designing Data-Intensive Applications*, Ch. 11 (stream vs batch trade-offs).
