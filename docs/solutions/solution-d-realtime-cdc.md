# Solution D — Real-Time CDC + Materialized Views

> **Status:** Architecture-only. Not implemented in this submission.
> **Positioning:** Highest freshness for downstream marketplace synchronization. Worth exploring when sub-second propagation from dealer inventory to eBay/Amazon listings becomes a revenue lever.

---

## Premise

Solution A handles streaming via PostgreSQL `LISTEN/NOTIFY` and BullMQ workers — sufficient for current scale but loses event durability beyond the queue retention window. Solution B introduces Redpanda + RisingWave as the modern streaming substrate.

Solution D is the **enterprise-grade marketplace-sync architecture**: every change in the catalog database is captured as a stream event via Debezium, materialized into denormalized views in Materialize or ksqlDB, and pushed to marketplace APIs through a sink with at-least-once semantics and per-listing deduplication.

This is the architecture you adopt when:

- Stock-out events must propagate to eBay/Amazon within seconds (not minutes)
- The marketplace integration is the primary revenue engine
- Catalog mutation rate exceeds 100 events per second sustained
- Audit and replay of every marketplace sync event is a compliance requirement

---

## Stack

| Layer                | Choice                                | Why                                                                |
| -------------------- | ------------------------------------- | ------------------------------------------------------------------ |
| Source database      | PostgreSQL (Solution A or B)          | Reuse existing serving layer                                       |
| CDC                  | Debezium PostgreSQL connector         | Logical replication slot — captures every row change                |
| Message broker       | Apache Kafka or Redpanda              | Durable event log; multi-day retention                              |
| Schema registry      | Confluent or Apicurio                 | Avro/Protobuf contracts between producers and consumers             |
| Materialized views   | Materialize (or ksqlDB)                | Incremental view maintenance; SQL interface                         |
| Analytical store     | ClickHouse                             | Sub-second OLAP over events for dashboards and ML                   |
| Sink connectors      | Kafka Connect (eBay/Amazon REST sinks) | Configurable retry policies, per-partition ordering                 |
| Observability        | OpenLineage + DataHub                  | Cell-level lineage from source row to marketplace listing           |
| Dead-letter handling | Custom DLQ topic + replay worker       | Per-event replayability after marketplace API hiccups               |

---

## Architecture

```
              ┌────────────────────────────────────────────────────────────┐
              │  Source of truth: PostgreSQL                               │
              │  (catalog table from Solution A or B)                      │
              └────────────────────────────┬───────────────────────────────┘
                                           │ logical replication slot
                                           ▼
              ┌────────────────────────────────────────────────────────────┐
              │  Debezium PostgreSQL connector                             │
              │   ├─ Captures every INSERT / UPDATE / DELETE              │
              │   ├─ Encodes as Avro per schema registry contract         │
              │   └─ Publishes to Kafka topics:                            │
              │      catalog.products, catalog.product_images, ...         │
              └────────────────────────────┬───────────────────────────────┘
                                           │
              ┌────────────────────────────▼───────────────────────────────┐
              │  Apache Kafka / Redpanda                                   │
              │  • 7-day retention                                          │
              │  • Compacted topics for current-state queries               │
              │  • Schema registry contracts (Avro)                         │
              └────────┬───────────────────────────────────┬───────────────┘
                       │                                   │
        ┌──────────────▼──────────────┐         ┌──────────▼──────────────┐
        │  Materialize                 │         │  ClickHouse              │
        │  (streaming SQL views)       │         │  (OLAP analytics)        │
        │                              │         │                          │
        │  CREATE MATERIALIZED VIEW    │         │  Aggregate facts:        │
        │    marketplace_live_listings │         │   • Daily stock changes  │
        │  AS SELECT ... FROM          │         │   • Pricing trends       │
        │    products_stream           │         │   • Marketplace sync     │
        │    JOIN inventory_stream     │         │     latency P99          │
        │    JOIN pricing_stream       │         │                          │
        │                              │         │  Backs Grafana / Metabase│
        │  Sub-second updates on       │         │  dashboards              │
        │  every upstream event        │         │                          │
        └──────────────┬───────────────┘         └──────────────────────────┘
                       │
        ┌──────────────▼─────────────────────────────────────────────────┐
        │  Kafka Connect sinks                                            │
        │   ├─ eBay Trading API sink (per-listing partition ordering)    │
        │   ├─ Amazon SP-API sink                                         │
        │   ├─ Google Shopping Content API sink                           │
        │   └─ Internal search index (OpenSearch) sink                    │
        │                                                                 │
        │  Each sink:                                                     │
        │   • At-least-once delivery semantics                            │
        │   • Configurable retry with exponential backoff                 │
        │   • DLQ topic for failures requiring human review               │
        │   • Per-partition rate limiting (eBay 5 calls/sec, etc.)        │
        └────────────────────────────────────────────────────────────────┘
```

---

## What you gain

| Property                            | Solution A          | Solution D                                    |
| ----------------------------------- | ------------------- | --------------------------------------------- |
| Stock-out propagation to marketplace| 30 seconds – minutes | **Under 1 second**                            |
| Catalog event replay (audit)        | 7-day audit table   | **Forever** (compacted Kafka topics)          |
| Cross-marketplace dedup             | App-layer logic     | **Schema-registry-enforced contracts**         |
| Analytical depth                    | Postgres ad-hoc     | **ClickHouse sub-second OLAP on millions of events** |
| Backfill of historical state        | pg_dump restore     | **Kafka topic replay from offset 0**          |
| Schema evolution governance         | Migration coordination | **Backward/forward-compat enforced at registry** |

---

## What you give up

- **Operational complexity.** Kafka + Debezium + Materialize + ClickHouse + Connect is five distinct systems with their own monitoring, scaling, and failure modes.
- **Cost.** Confluent Cloud or self-hosted Kafka cluster (3-broker minimum) + ClickHouse Cloud + Materialize all bill seriously at scale.
- **Talent market.** Engineers with production Kafka + streaming-SQL experience are scarcer than TypeScript engineers by an order of magnitude.
- **Initial setup time.** Multi-week project to get from zero to first marketplace sync.

---

## When to choose Solution D

Adopt when **all four** of these hold:

1. Marketplace synchronisation is the highest-revenue function of the system
2. Stock-out propagation latency is contractually measured (SLA penalties exist)
3. The team has at least one engineer with production Kafka experience
4. Annual cloud budget exceeds $50k (Confluent Cloud + ClickHouse Cloud baseline)

If any one of these is false, Solution B's Redpanda + RisingWave gets you 80 percent of the benefit at 20 percent of the operational cost.

---

## Implementation budget (AI-assisted estimate)

- Debezium connector deploy + Postgres replication slot config: ~4 hours
- Kafka cluster (3-broker) provisioning + schema registry: ~8 hours
- Materialize deploy + 3 streaming SQL views: ~6 hours
- ClickHouse cluster + ingest from Kafka: ~6 hours
- Three sink connectors (eBay, Amazon, Google) with retry/DLQ: ~12 hours
- Observability (OpenLineage + DataHub) wiring: ~6 hours

**Total: ~42 hours AI-assisted, or ~3-4 weeks manual.**

---

## How Solution D relates to A and B

Solution D **builds on top of** Solution A or B, not replaces them. The source of truth is still the PostgreSQL catalog table. Debezium captures changes from there and fans them out to the streaming substrate.

Migration path: A → B → D, each layer additive. Catalog API in Solution A keeps serving reads throughout.
