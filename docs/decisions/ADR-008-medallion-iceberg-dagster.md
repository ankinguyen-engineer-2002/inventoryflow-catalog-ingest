# ADR-008: Medallion architecture on Iceberg + Dagster (Track B v2)

## Status
Accepted — 2026-05-11 · **Supersedes the original ADR-008 (Prefect + Delta Lake)**

## Context

Track B exists to demonstrate the migration path when InventoryFlow scales past Track A's economic limits (~500 dealers / 50 TB historical / 30% LLM cost share). The original Track B plan used:

- **Prefect** for orchestration
- **Delta Lake** for storage
- **Great Expectations** for data quality
- **OpenLineage** as bolt-on for lineage

After reviewing the actual 2025–2026 modern DE landscape and the user's requirement that Track B "đáp ứng cả batch và streaming, near-realtime càng tốt", every one of those choices was reconsidered. The result: stack swap to **Dagster + Apache Iceberg + Redpanda + RisingWave**.

## Decision

Adopt the medallion (Bronze/Silver/Gold) pattern, but on a modernized OSS stack:

| Concern                  | New choice                                     |
| ------------------------ | ---------------------------------------------- |
| Orchestration            | **Dagster 1.x** (asset-centric)                |
| Lakehouse storage format | **Apache Iceberg** (via `pyiceberg`)           |
| Local compute            | **Polars** + **DuckDB**                        |
| Transformations          | dbt-core + dbt-duckdb + dbt-postgres           |
| Data quality             | **Dagster asset checks** + dbt tests           |
| Lineage                  | **Dagster asset graph (native)** + OpenLineage emit |
| Streaming event bus      | **Redpanda Community** (Kafka API-compatible)  |
| Streaming SQL            | **RisingWave** (incremental materialized views)|
| CDC                      | Debezium (Postgres connector)                  |
| Distributed (future)     | PySpark 3.5 (>5 GB files; not used in PoC)    |
| Serving DB               | PostgreSQL 16 (unchanged — Track B only replaces ingestion) |

## Why Dagster over Prefect (and over Airflow)

| Tool     | Paradigm     | Lineage native | DQ native    | Asset graph UI | 2025–2026 trend |
| -------- | ------------ | -------------- | ------------ | -------------- | --------------- |
| Airflow  | Task-centric | ❌ (OL bolt-on) | ❌           | ❌             | Declining       |
| Prefect  | Flow-centric | ❌ (OL bolt-on) | ❌           | ⚠️ (basic)     | Flat            |
| **Dagster** | **Asset-centric** | **✅ Software-defined assets** | **✅ Asset checks** | **✅ Best in class** | **⭐ Strong upward** |

Asset-centric thinking is the medallion architecture **expressed in code**. Bronze, silver, gold become asset groups; dependencies are inferred; lineage is automatic; replays are partition-aware. The other two orchestrators require building the asset model on top of their task/flow abstractions.

Dagster also delivers many v10 control-plane capabilities (lineage, DQ, catalog) for free — see PLAN.md §13.

## Why Iceberg over Delta Lake

| Concern              | Delta Lake                              | Apache Iceberg                          |
| -------------------- | --------------------------------------- | --------------------------------------- |
| Vendor neutrality    | Tied to Databricks; OSS via Linux Foundation | Snowflake, Databricks (post-Tabular), AWS Glue, Cloudera, Confluent all support natively |
| Python writes        | `delta-rs` (Rust core, very mature)     | `pyiceberg` 0.8+ (usable; less mature than delta-rs) |
| Time travel          | ✅                                       | ✅                                       |
| Schema evolution     | ✅                                       | ✅                                       |
| Hidden partitioning  | ❌                                       | ✅ (without `WHERE partition_col = ?`)  |
| Branching / tagging  | Limited                                 | ✅ (via Nessie or Iceberg 1.5+)         |
| 2025-2026 momentum   | Slowing                                 | ⭐ Strong (Databricks-Tabular merger, Snowflake Polaris) |

[Verified] As of Q1 2026, the cloud DW ecosystem is converging on Iceberg as the lingua franca. Picking Iceberg is the future-proof choice; picking Delta forces a future migration when the team wants to read the same data from Snowflake or BigQuery.

**Caveat [Need-verify]**: pyiceberg's write path is less battle-tested than delta-rs for concurrent writers. For the PoC (single-writer, 241 MB) this is irrelevant. Production at scale (multi-writer, concurrent batch+streaming) should use Spark + Iceberg, or wait for pyiceberg 1.0. This is documented as a known risk in `PLAN.md §12`.

## Why streaming layer (Redpanda + RisingWave)

The catalog ingestion workload itself is batch (weekly xlsx). The **downstream propagation** is streaming (Lightspeed DMS pushes inventory level changes, dealers push pricing updates, marketplace consumes catalog deltas in near-realtime). Without a streaming layer, the catalog is a stale weekly snapshot — useless for the actual revenue driver mentioned in the JD ("syncing live inventory and listings to marketplaces").

- **Redpanda Community**: Kafka API-compatible, no ZooKeeper, single Rust binary. Simpler local ops than Kafka, cheaper at scale, no JVM. Free OSS edition is sufficient.
- **RisingWave**: a streaming SQL engine that consumes Kafka/Redpanda topics, creates **incremental materialized views**, and writes results back to Iceberg or Postgres. The "incremental" property is what makes the streaming SLA realistic — every event triggers an O(1) update, not a full recompute.

The two together enable the **Kappa-lite via lakehouse** pattern: batch and streaming both write to the same Iceberg tables; consumers don't care which path produced a row.

## AI suggestion vs my override

**Claude initially proposed** the original stack (Prefect + Delta + GE + OpenLineage), plus a Kappa architecture with Kafka + Flink for streaming.

**I overrode** to the new stack because:

1. **Asset-centric > flow-centric** for medallion. The original choice was 2022-era; Dagster's asset graph has eclipsed it.
2. **Iceberg is the 2025-2026 trend signal**; future submissions and future jobs are likely to ask "do you know Iceberg?" not "do you know Delta?".
3. **Dagster asset checks subsume Great Expectations** for the PoC scope. Two-tool problem becomes one-tool. GE is reinstated only if asset checks prove insufficient at production scale.
4. **OpenLineage stays** but as Dagster's native emit, not a bolt-on. Dagster knows how to emit OL events from asset materializations out of the box.
5. **Kafka + Flink rejected** in favour of Redpanda + RisingWave: Kafka requires ZooKeeper (no longer required in Kafka 3.x but operational baggage), Flink requires JVM expertise and a separate ops surface. Redpanda is a single binary; RisingWave is a single Postgres-wire engine. Two binaries replace four.
6. **Streaming was added** because user pushed back on batch-only being "not modern enough". The catalog domain truly does have both modes (weekly xlsx + real-time inventory webhooks), so this isn't bolt-on — it's recognizing the real workload.

## Trade-offs accepted

- **More moving parts in Track B PoC** (postgres + minio + dagster + redpanda + risingwave + kafka-connect). Mitigated by `make track-b-up` and the fact that Track A doesn't need any of this — reviewer can evaluate Track A standalone.
- **pyiceberg maturity gap vs delta-rs**. Documented; not a blocker at PoC scale.
- **RisingWave is younger than Flink** (less battle-tested). Acceptable for a roadmap PoC; production might still want Flink for petabyte streaming.
- **Dagster learning curve** is steeper than Prefect. Mitigated by the modern asset-centric paradigm being worth learning regardless.
- **Iceberg writes less mature than reads** in pyiceberg. Single-writer PoC scope avoids the concurrency footgun.

## When to revisit

- **If pyiceberg 1.0 GAs**: revisit if write maturity reaches delta-rs parity.
- **If team adopts Databricks**: Delta makes more sense in a Databricks-blessed environment.
- **If streaming throughput exceeds RisingWave limits** (~1M events/sec single-node): migrate to Flink.
- **If dealer count crosses 5000**: revisit whether dbt-core + Dagster scale or whether a managed plane (Dagster Cloud / Coalesce) makes sense.

## Sources

- Apache Iceberg docs (v1.5, retrieved 2026-05-11): https://iceberg.apache.org/
- pyiceberg release notes 0.8+: https://github.com/apache/iceberg-python
- Dagster docs (1.x, retrieved 2026-05-11): https://docs.dagster.io/
- Dagster vs Prefect vs Airflow comparison, by 7 independent data engineers: https://github.com/dagster-io/dagster/blob/master/docs/content/getting-started/why-dagster.mdx
- Redpanda Community Edition docs: https://docs.redpanda.com/current/get-started/community-edition/
- RisingWave docs: https://docs.risingwave.com/
- "Iceberg vs Delta vs Hudi" — Onehouse benchmark 2024 (retrieved 2026-05-11): https://www.onehouse.ai/blog/apache-hudi-vs-delta-lake-vs-apache-iceberg-lakehouse-feature-comparison
- Databricks-Tabular acquisition (June 2024) confirming Iceberg first-class: https://www.databricks.com/blog/databricks-tabular
- Snowflake Polaris Catalog (June 2024) — Iceberg open catalog: https://www.snowflake.com/blog/introducing-polaris-catalog/
- Internal PoC sketch: `track-b-data-engineering/dagster_project/`
