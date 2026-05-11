# ADR-010: Batch + near-realtime streaming hybrid (both tracks)

## Status
Accepted — 2026-05-11

## Context

The original plan treated catalog ingestion as a pure batch problem: dealer uploads xlsx, pipeline processes, done. But the **revenue-driving** part of InventoryFlow's business — per the JD's own wording: *"syncing live inventory and listings to marketplaces like eBay and Amazon"* — is fundamentally **streaming**:

- Lightspeed DMS pushes inventory level changes via webhook (real-time).
- Dealers adjust pricing → must propagate to eBay/Amazon within minutes.
- Customers buy a part → stock-out event → must remove listing within seconds.
- New SKU added by dealer → must publish to marketplaces within hour.

A pure-batch catalog is a stale weekly snapshot. The system needs both modes within one cohesive control plane.

## Decision

Both tracks support **batch + near-realtime streaming**. The pattern differs by track because the stacks are different, but the semantics are the same:

### Track A (within JD stack)

| Concern        | Implementation                                                              |
| -------------- | --------------------------------------------------------------------------- |
| Inbound webhooks | Fastify routes `/events/{inventory|pricing|order}` with JWT-verified dealer_id |
| Async dispatch | `BullMQ` separate **stream queues** (conc=32, no LLM) vs **batch queues** (conc=8, with LLM) |
| Inter-process push | PostgreSQL `LISTEN/NOTIFY` for in-DB broadcast to catalog API + marketplace sync worker |
| Outbound CDC (optional) | Debezium Postgres connector + Redpanda topic — opt-in via env flag |
| Reliable delivery | `stream_outbox` table — outbox pattern, transactional with business writes |
| SLA            | Webhook → DB write **<500 ms p95**; webhook → marketplace API call **<5 s p95** |

### Track B (modern OSS DE)

| Concern        | Implementation                                                              |
| -------------- | --------------------------------------------------------------------------- |
| Event bus      | **Redpanda Community** (Kafka-API, single Rust binary, no ZooKeeper)        |
| Stream processor | **RisingWave** — streaming SQL, incremental materialized views, Postgres wire |
| Sink           | RisingWave `SINK INTO iceberg.gold_live_view` — same Iceberg tables as batch |
| Source bridge  | Dagster `@sensor on_redpanda_topic` triggers asset re-materialization for periodic snapshots |
| CDC            | Debezium → Redpanda topic `postgres.cdc.*`                                  |
| SLA            | Webhook → Redpanda **<100 ms**; Redpanda → RisingWave MV **<1 s**; MV → Iceberg sink **<5 s** |

Both tracks share the **Kappa-lite via lakehouse** principle (Track B explicitly, Track A implicitly via PG as single source of truth): one storage layer, batch and stream both write to it, consumers don't care which path produced a row.

## AI suggestion vs my override

**Claude initially suggested** Kafka + Flink for streaming. The user pushed back asking for modern + lightweight stack matching 2026 trends.

**I overrode** to Redpanda + RisingWave because:

1. **Kafka requires JVM + ZooKeeper or KRaft mode operational complexity**. Redpanda is a single Rust binary, single broker for PoC.
2. **Flink requires JVM + checkpoint storage + JobManager+TaskManager separation**. RisingWave is a single Postgres-wire engine, no JVM, queries via standard SQL.
3. **Streaming SQL is more accessible than DataStream APIs** for a team that already knows dbt/SQL.
4. **For PoC scale**, Redpanda Community Edition + single-binary RisingWave is sufficient. Kafka + Flink can be the **upgrade path** documented in this ADR's "When to revisit" section.
5. **For Track A**, I rejected Kafka entirely — it's overkill within a JD that says PostgreSQL + Redis. PG `LISTEN/NOTIFY` + BullMQ stream queues cover the entire requirement within stack constraints. Redpanda becomes an **opt-in extension** for outbound CDC only.

## The outbox pattern (important detail)

For Track A, naive flow is:
1. Webhook arrives → write to DB → publish to Redpanda → marketplace sync.

But what if step 3 fails after step 2 succeeds? The DB and message bus drift.

**Solution**: outbox pattern — in the same DB transaction that writes business data, write the event to a dedicated `stream_outbox` table. A separate poller publishes from `stream_outbox` to Redpanda with at-least-once semantics, marking `published_at` on success.

This makes "write to DB" and "publish event" **transactionally atomic** without needing distributed XA transactions.

```sql
BEGIN;
UPDATE products SET stock_level = $1 WHERE part_number = $2;
INSERT INTO stream_outbox (topic, payload) VALUES ('inventory.changes', $3);
COMMIT;
-- Background worker drains outbox to Redpanda.
```

For Track B, the equivalent is **Iceberg + Debezium CDC**: Debezium reads the Postgres WAL, publishes every committed change to Redpanda. No outbox table needed because the WAL **is** the durable log.

## Trade-offs accepted

- **Streaming layer in Track A is opt-in** (`STREAMING_ENABLED=true`) to keep the default reviewer experience to 3-container `docker compose up`. Full streaming requires adding Redpanda container.
- **At-least-once semantics** for streaming events; deduplication is consumer responsibility. Acceptable; idempotency primitives (SHA-256, `ON CONFLICT`) handle dedup at the DB layer.
- **No ordering guarantees across topics**; per-topic-partition order is preserved. Consumers needing cross-topic order use timestamps.
- **Outbox table growth**: needs periodic vacuum/archive. Documented in `docs/runbook.md`.
- **RisingWave + Redpanda + Kafka Connect** is a 3-container streaming stack — not zero-op, but lighter than Kafka + Flink + Schema Registry + ZooKeeper.

## When to revisit

Switch from Redpanda → Kafka if:
- Multi-broker fault tolerance required at >10k events/sec sustained throughput.
- Need ksqlDB, Schema Registry, Kafka Connect ecosystem maturity.

Switch from RisingWave → Flink if:
- Stream processing throughput exceeds RisingWave single-node ~1M events/sec.
- Need stateful stream-stream joins beyond Postgres-equivalent SQL semantics.

Switch from PG LISTEN/NOTIFY → Redpanda in Track A if:
- Stream consumers number more than ~50 (PG LISTEN scaling limit).
- Need durable replay of events across consumer restarts.

## Sources

- Redpanda docs: https://docs.redpanda.com/current/ (retrieved 2026-05-11).
- RisingWave docs: https://docs.risingwave.com/ (retrieved 2026-05-11).
- Outbox pattern: https://microservices.io/patterns/data/transactional-outbox.html (retrieved 2026-05-11).
- Debezium Postgres connector: https://debezium.io/documentation/reference/stable/connectors/postgresql.html (retrieved 2026-05-11).
- "Kappa Architecture" originally described by Jay Kreps (LinkedIn co-founder).
- PostgreSQL LISTEN/NOTIFY docs: https://www.postgresql.org/docs/16/sql-listen.html (retrieved 2026-05-11).
