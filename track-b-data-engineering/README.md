# Track B — Modern OSS Data Engineering (proof of concept)

> Track B documents the migration target for InventoryFlow once Track A's scaling triggers fire (ADR-009). It is **not** the submission recommendation — Track A is. Track B exists to demonstrate concrete fluency with the 2025-2026 data-engineering stack rather than a hand-waved migration plan.

---

## Scope

This is a proof of concept, not a production deployment. The scaffold demonstrates the full stack — Dagster for asset orchestration, Apache Iceberg for the lakehouse layer, Polars for compute, dbt for SQL transformations, Redpanda for the streaming event bus, RisingWave for streaming SQL, and DuckDB for ad-hoc analytics. End-to-end execution is wired but execution against real data is exercise-left-to-the-reviewer because the stack requires a six-container Docker compose to come up.

| Component                                                | Status                    |
| -------------------------------------------------------- | ------------------------- |
| `docker-compose.yml` with six services                   | Defined                   |
| Dagster project (resources, three medallion assets, two asset checks) | Defined          |
| Iceberg bronze + silver + gold tables created on first materialisation | Wired           |
| Polars reads xlsx → bronze                               | Implemented (demo-grade)  |
| Silver transformation via Polars                         | Implemented (demo-grade)  |
| Gold mart with fitment JSON shape                         | Implemented (demo-grade)  |
| dbt project structure                                    | Folder only; production work |
| RisingWave streaming SQL (`streaming/risingwave_views.sql`) | Defined                |
| Redpanda event seeder (`streaming/redpanda_seed.py`)     | Defined                   |
| DuckDB analytical demo (`notebooks/duckdb_iceberg_demo.py`) | Defined                |
| dbt project (`dbt/`) with three silver + two gold models and dbt_utils tests | Implemented |
| Python-ported section detector (`dagster_project/section_detector.py`) | Implemented |
| pytest suite (`tests/test_section_detector.py`)          | 8 tests, mirrors Track A surface |

---

## Why this stack

Each choice is documented in an ADR; see `../docs/decisions/`:

- **Dagster over Prefect or Airflow** — asset-centric paradigm matches medallion architecture; lineage and data-quality checks are first-class concepts rather than bolted-on. ADR-008.
- **Apache Iceberg over Delta Lake** — vendor neutrality after the Databricks-Tabular acquisition and Snowflake's Polaris catalog. Iceberg tables are queryable from Snowflake, Databricks, BigQuery, Trino, and DuckDB through the same REST catalog protocol. ADR-008.
- **Polars over pandas** — five to thirty times faster for the file size used here; zero JVM dependency. ADR-008.
- **Redpanda over Apache Kafka** — Kafka-API compatible, written in Rust, no ZooKeeper requirement; single-binary deployment. The Community Edition is free for production use up to three brokers. ADR-010.
- **RisingWave over Apache Flink** — streaming SQL is more accessible than the Flink DataStream API; the engine speaks the PostgreSQL wire protocol so any existing SQL tooling works. ADR-010.
- **Gemini provider stays out of scope** — Google's free-tier terms permit training on API content, which is incompatible with production dealer data. ADR-007.

---

## When to migrate Track A to Track B

From ADR-009. Migrate ingestion (not serving) when any of these conditions holds for two consecutive months:

1. Active dealer count exceeds 500
2. Historical data volume exceeds 50 terabytes
3. LLM cost share exceeds 30 percent of monthly cloud bill
4. Analytics queries on the products table cause catalog-API p95 latency above 100 milliseconds
5. Dealer schema changes occur at one or more per week
6. Business requires sub-one-hour recovery time objective from a data-corruption incident

---

## How to run it

Prerequisites: Python 3.12, Poetry 1.8+, Docker, `psql` client.

```bash
cd track-b-data-engineering

# Boot the six-container stack (MinIO, Iceberg REST, Postgres, Redpanda, RisingWave)
make up

# Wait approximately 30 seconds for services to become healthy
docker-compose ps

# Install dependencies and create the Iceberg namespace
make bootstrap

# Run the batch pipeline: bronze → silver → gold
make track-b-batch

# Apply streaming SQL views and seed sample events into Redpanda
make track-b-stream

# Run the DuckDB analytical query demonstration
make track-b-query

# Open the Dagster webserver (asset graph, lineage, run history)
make dagster-dev
# Visit http://localhost:3000
```

---

## Repository layout

```
track-b-data-engineering/
├── README.md                       ← this file
├── pyproject.toml                  ← Poetry dependencies
├── docker-compose.yml              ← six-service local stack
├── Makefile                        ← convenience targets
├── dagster_project/
│   ├── __init__.py
│   ├── resources.py                ← Iceberg catalog, Postgres, source xlsx
│   ├── assets.py                   ← bronze + silver + gold assets
│   ├── asset_checks.py             ← data quality gates (replaces GE)
│   └── definitions.py              ← Dagster entry point
├── dbt/
│   └── (folder reserved for dbt-duckdb models; gold-layer SQL)
├── streaming/
│   ├── risingwave_views.sql        ← CREATE SOURCE + MV + SINK
│   └── redpanda_seed.py            ← sample event publisher
├── notebooks/
│   └── duckdb_iceberg_demo.py      ← DuckDB-on-Iceberg analytics
├── orchestration/
│   └── (folder reserved for Dagster sensors and schedules)
├── tests/
│   └── (folder reserved for pytest suite)
└── scripts/
    └── (folder reserved for ops scripts)
```

---

## Limitations of this proof of concept

This is honest about what is and isn't implemented:

- The Polars-driven silver transformation is a demonstration. Production silver would port the Track A section detector to Python or invoke it via subprocess for reuse.
- The gold mart fitment column is a placeholder constant rather than a real per-row derivation. A full implementation joins silver against a vehicle-models dimension Iceberg table.
- The dbt project structure exists as folders only. The full dbt-duckdb-on-Iceberg integration is approximately one engineering day; documented as the next milestone.
- The RisingWave-to-PostgreSQL JDBC sink requires the `live_inventory_view` table to exist in PostgreSQL. Schema is documented but the migration is not yet written.
- No load test, no benchmark numbers. Track A's measured numbers are the reference until Track B is exercised under production traffic.

These limitations exist because Track B is positioned as a documented migration target, not a parallel implementation. The scaffold runs end-to-end on the reference xlsx; production hardening is approximately one to two engineering weeks.

---

## How Track A and Track B coexist

Track B replaces only the ingestion plane. The serving plane (PostgreSQL, Fastify catalog API, Track A streaming workers) remains unchanged. Track B's gold-layer Iceberg tables synchronise to the same PostgreSQL database that Track A populates today, via `dbt-postgres` or change-data-capture (Debezium).

This means:

- Customer-facing services (`POST /events/inventory`, `GET /products?fitment=...`) keep their existing implementation throughout the migration.
- Track B can run alongside Track A in shadow mode for weeks before any cut-over.
- Each dealer can be migrated independently by reconfiguring its `dealer_pattern_bindings` row.

The migration playbook is in `../docs/decisions/ADR-009-when-to-switch-tracks.md`.
