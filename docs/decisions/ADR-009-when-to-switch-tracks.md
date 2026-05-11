# ADR-009: Trigger criteria for migrating Track A → Track B

## Status
Accepted — 2026-05-11

## Context

Track A (TypeScript) is the recommended submission. Track B (Polars + Delta) exists as the documented migration target. Without explicit trigger criteria, "switch when it's needed" is wishful — engineering teams either over-engineer too early or wake up in a fire when scale bites.

A senior recommendation requires **observable, quantitative thresholds** that a non-author can verify.

## Decision

Migrate **ingestion only** (serving stays on PostgreSQL) when **any** of the six triggers below fires for two consecutive months. Two-month confirmation prevents one-off spikes from triggering a costly migration.

### Trigger 1 — Dealer count

```
ACTIVE_DEALERS > 500
```

Measured as `COUNT(DISTINCT dealer_id) WHERE last_ingest_at > now() - interval '30 days'`.

Rationale: Track A's BullMQ queue-per-dealer pattern hits Redis memory pressure (~32GB queue state) around this number on commodity instances.

### Trigger 2 — Historical volume

```
TOTAL_BRONZE_EQUIVALENT > 50 TB
```

If Track A were Track B, what would the bronze layer be? Approximated as `(file_count × avg_file_size) × 1.3 (decompression factor)`.

Rationale: PostgreSQL TOAST + index storage costs cross break-even with object storage + Delta around 50 TB.

### Trigger 3 — LLM cost share

```
MONTHLY_LLM_COST / TOTAL_CLOUD_COST > 30%
```

When LLM dominates the bill, **global dedup pays for the migration in 1–2 months**. Track B's canonical `parts_global` table caches translations once per part-number across all dealers.

### Trigger 4 — OLAP-OLTP contention

```
catalog_api_p95_latency_during_analytics_windows > 100 ms
```

When analytics queries (e.g., "show price-change history") start blocking the catalog API, the workloads must be split. Track B's gold layer on object storage queryable by DuckDB removes the contention.

### Trigger 5 — Schema churn

```
DEALER_SCHEMA_CHANGES_PER_WEEK >= 1
```

When dealers add/rename columns weekly, Drizzle migrations become a bottleneck. Delta `mergeSchema=true` absorbs drift automatically + alerts via OpenLineage.

### Trigger 6 — Recovery objective shift

```
BUSINESS_RTO_REQUIREMENT < 1 hour
```

When the business requires bit-perfect recovery from a corrupted-data incident in <1 hour, Delta time travel is the only credible answer. Postgres PITR can't replay schema-level corruption cleanly.

## What the migration looks like (operationally)

- **Week 0**: Stand up MinIO/R2 + Prefect + dbt repo (already scaffolded in Track B).
- **Weeks 1–2**: Run Track B in **shadow mode** — ingest the same files as Track A, write to bronze. Don't sync to PostgreSQL yet.
- **Week 3**: Compare bronze + silver outputs against Track A's PostgreSQL state. Reconcile diffs.
- **Week 4**: Switch one dealer to dbt-postgres sync as primary; freeze Track A for that dealer.
- **Weeks 5–8**: Gradually shift remaining dealers. Track A becomes the catalog-API layer only.
- **Week 9+**: Track A code shrinks to API + worker for catalog-API operations. Ingestion code retired.

**The customer-facing layer (Fastify catalog API, marketplace sync) never changes during this migration.** That's the architectural payoff of keeping PostgreSQL as the serving plane in Track B.

## AI suggestion vs my override

**Claude initially suggested** a single trigger ("when current architecture starts breaking, migrate").

**I overrode** because:

1. "Starts breaking" is unobservable. Engineering teams need **measurable thresholds** to plan migrations rather than firefight them.
2. Six triggers cover the dimensions that actually matter: load (1, 2), cost (3), contention (4), agility (5), reliability (6). Any one being satisfied is sufficient — they're disjunctive, not conjunctive.
3. Two-month confirmation prevents knee-jerk migration off a single spike.
4. Senior-level recommendations require **non-author verifiability**.

## Trade-offs accepted

- **Six triggers is more complex** than one rule. Mitigated: a simple monthly review job computes all six and posts to Slack. The review is automated; the decision is the team's.
- **Threshold values are estimates** — the exact numbers (500, 50 TB, 30%, etc.) are based on industry benchmarks and Track A's design limits, not measured on InventoryFlow specifically. Should be revisited after the first 3 months of production data.
- **"Two-month confirmation" delays response by up to 60 days**. Acceptable for migration (multi-week effort itself); not acceptable for incident triggers (handled separately, not here).

## When to revisit

- After 3 months of production Track A data: tune the threshold numbers based on observed Track A behaviour.
- If a new trigger emerges that wasn't on the list (e.g., regulatory requirement for immutable audit logs), add as Trigger 7.

## Sources

- Track A architectural limits in [PLAN.md §4.6](../../PLAN.md#46-scaling-story-when-does-track-a-break).
- BullMQ queue memory profiling: https://docs.bullmq.io/guide/queues/auto-removal-of-jobs (retrieved 2026-05-11).
- Postgres TOAST limits: https://www.postgresql.org/docs/16/storage-toast.html (retrieved 2026-05-11).
- Cost benchmark — Anthropic Batch (50% off): https://docs.anthropic.com/en/docs/build-with-claude/batch-processing (retrieved 2026-05-11).
- Delta time travel doc: https://docs.delta.io/latest/delta-batch.html#-deltatimetravel (retrieved 2026-05-11).
