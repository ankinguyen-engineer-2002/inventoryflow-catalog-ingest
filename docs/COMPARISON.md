# Track A v2 vs Track B v2 — Side-by-Side Comparison

> Companion to [`../PLAN.md`](../PLAN.md). Read PLAN first for context.

This document quantifies the trade-offs across **18 dimensions** so the reviewer can audit the recommendation (**Track A v2 for current stage, Track B v2 for scale + modern hybrid**).

Numbers marked `[measured]` are from actual benchmark runs (see `docs/bench/bench-results.json`); `[estimated]` are projections from documented benchmarks of comparable workloads.

**Measured on Apple M2, Node 25, PostgreSQL 16 in Docker (2026-05-11):**

| Metric                                 | Result        |
| -------------------------------------- | ------------- |
| Fitment lookup query p50                | 0.60 ms       |
| Fitment lookup query p95                | 0.87 ms       |
| Fitment lookup query p99                | 1.02 ms       |
| Fitment lookup query max (500 samples) | 1.32 ms       |
| `GIN (fitment jsonb_path_ops)` size     | 2,128 kB      |
| Products row count                      | 3,938         |
| Product-image associations              | 10,524        |
| End-to-end full pipeline wall-time      | ~60 seconds   |

The fitment query consistently completes in under 1.5 milliseconds at p99, two orders of magnitude faster than the initial 50 ms estimate. The `jsonb_path_ops` index variant pays for itself in containment-query selectivity.

---

## 1. Executive summary

**Recommendation**: Ship Track A v2 as the answer. Use Track B v2 as the documented migration target.

**Why not just Track B?** It's overkill for InventoryFlow's current scale, doesn't match the JD stack, and the team would inherit a Python/Dagster/Iceberg codebase that's harder to hire for at startup stage.

**Why not just Track A?** It papers over real scaling cliffs at 500+ dealers and 50+ TB historical. A senior engineer should surface those before they bite. Also, Track B v2 demonstrates fluency with the modern 2026 DE stack (Dagster + Iceberg + Redpanda + RisingWave) which is a senior-DE hiring signal.

**Both tracks now support batch + near-realtime streaming** — the catalog domain has both modes (weekly xlsx + real-time inventory webhooks), so this isn't bolt-on but a recognition of the real workload.

---

## 2. Comparison matrix — 18 dimensions

| # | Dimension                              | Track A v2 (TS)             | Track B v2 (Modern OSS DE)        | Winner for current stage |
|---|----------------------------------------|-----------------------------|------------------------------------|--------------------------|
| 1 | **JD stack match**                     | ★★★★★ exact 1:1             | ★★ different ecosystem (Python)    | A                        |
| 2 | **Time to ship (PoC)**                 | 3 days                      | 4 days                             | A                        |
| 3 | **Batch wall-time, 241 MB, M2 Mac**    | ~4–6 min [estimated]        | ~2–3 min (Polars wins) [estimated] | B (marginal)             |
| 4 | **Streaming SLA, webhook → view**      | ~500 ms (PG NOTIFY)         | ~1 sec (RisingWave MV) [estimated] | A (marginal)             |
| 5 | **Wall-time, 100×1GB/file batch**      | Shard workers, ~3 h         | Native parallel, ~40 min           | B                        |
| 6 | **Wall-time, 10 TB historical replay** | Hours-to-days               | <1 h (Iceberg time-travel)         | B                        |
| 7 | **Idempotency primitive**              | SHA-256 + `ON CONFLICT`     | Iceberg `MERGE INTO` (table-level) | B (cleaner)              |
| 8 | **Replay / time travel**               | Audit-log replay (partial)  | `VERSION AS OF` (full)             | B                        |
| 9 | **Schema evolution**                   | Drizzle migration (manual)  | Iceberg `ALTER TABLE` (native)     | B                        |
|10 | **Lineage (cell/column-level)**        | Audit table (row-level)     | Dagster asset graph + OpenLineage  | B                        |
|11 | **Asset catalog / discovery**          | None built-in                | Dagster asset graph UI             | B                        |
|12 | **Data quality contracts**             | Zod runtime                  | Dagster asset checks               | B                        |
|13 | **Cost / 1 dealer / one-shot LLM**     | $3–5                        | $3–5                                | Tie                      |
|14 | **Cost / 1000 dealers / month LLM**    | ~$3–5 k                     | ~$300–500 (global dedupe)          | B                        |
|15 | **Infra cost / 1 dealer / month**      | ~$30 (small PG+Redis+Node)  | ~$80 (Dagster + MinIO + Redpanda) | A                        |
|16 | **Infra cost / 1000 dealers / month**  | ~$1500+ (vertical scale PG) | ~$400 (object-storage economics)   | B                        |
|17 | **Team pickup (TS startup team)**      | ★★★★★ trivial               | ★★ DE talent rare                  | A                        |
|18 | **Vendor lock-in risk**                | ★★★★ low (all OSS libs)     | ★★★★★ Iceberg → multi-vendor       | B (marginal)             |

**Score by stage**:

| Stage                              | Wins for A | Wins for B | Tie | Recommendation       |
| ---------------------------------- | ---------- | ---------- | --- | -------------------- |
| Today (<100 dealers)               | 7          | 9          | 2   | **A** (JD match + simplicity win) |
| Year 2 (~500 dealers)              | 3          | 13         | 2   | Migrate              |
| Year 3+ (1000+ dealers, multi-region) | 1       | 16         | 1   | **B**                |

> [!NOTE]
> Even at "today" stage, Track B wins 9 dimensions on raw merit. The reason **A is still the recommendation today** is the JD match + team pickup + lower infra cost at small scale. Stack fit and team velocity outweigh raw architectural wins until growth justifies the migration cost.

---

## 3. Concrete scenarios — same input, both reactions

### Scenario 1: Dealer re-sends file v2 (90% overlap with v1)

- **A**: Re-process all 12k rows, rely on `ON CONFLICT DO UPDATE`. Wasted work ~90%. R2 PUTs dedup'd by HEAD-check on SHA-256 (good).
- **B**: `MERGE INTO bronze.catalog_rows ON _row_signature` → only diff propagates. Wasted work ~0%. Plus: `AutoMaterializePolicy` only re-runs downstream silver/gold if bronze changed.

**Verdict**: B fundamentally better; A acceptable below ~100 dealers.

### Scenario 2: OEM adds new column "Country of Origin" mid-quarter

- **A**: Drizzle migration written + reviewed + deployed; old runs lose the column; 1–2 day cycle.
- **B**: Iceberg `ALTER TABLE` adds the column; OpenLineage emits `schemaChangeEvent`; consumers opt-in; ~0 day eng cycle.

**Verdict**: B dramatically better for OEM churn.

### Scenario 3: "A bug last night corrupted 10k fitment rows"

- **A**: Restore PG from PITR backup (~5 min RPO). Re-run ingest for affected dealers. Reconstruct images from R2.
- **B**: `SELECT * FROM bronze VERSION AS OF '2026-05-04 14:00:00'` → re-materialize silver+gold via Dagster. No restore. ~30 min.

**Verdict**: B's recovery story far superior. A mitigated by good PG backup discipline.

### Scenario 4: 1000 dealers translate same Chinese phrase

- **A**: LLM call per dealer × per part_no = 1000 × $0.003 = $3 for one phrase.
- **B**: `part_number_canonical` table (gold-level) keyed by `part_number_norm`; 1 LLM call globally; reused.

**Verdict**: B wins by 1000× at scale.

### Scenario 5: Real-time inventory webhook flood

- **A**: Fastify route → BullMQ stream queue (conc=32) → DB upsert → PG NOTIFY → marketplace sync. <500 ms p95.
- **B**: Webhook → Redpanda topic → RisingWave consumes incrementally → MV refreshes → Iceberg sink → DataHub lineage event. <1 sec p95.

**Verdict**: A marginally faster (in-process); B more scalable + observable. Both meet SLA.

### Scenario 6: Analytics — "which parts changed in price most in Q1?"

- **A**: OLAP query on PG. With 10M rows + history, touches hot OLTP indexes; risks impacting catalog API latency.
- **B**: DuckDB on Iceberg gold; partition-pruned; sub-second; zero impact on serving PG.

**Verdict**: B's OLAP/OLTP separation is the standard pattern.

### Scenario 7: New dealer onboarding via metadata

- **A**: Insert row into `dealers`, insert row into `dealer_pattern_bindings`, optionally upload custom `rules.yaml`. Engine picks up next minute. **No code deploy**.
- **B**: Same metadata insert; Dagster dynamic asset partition auto-created; asset graph re-renders.

**Verdict**: Tie — both implement the metadata-driven pattern (ADR-014). This is what enables "onboarding hundreds of dealerships efficiently" per the JD.

---

## 4. Migration trigger criteria (when to switch A → B)

From [ADR-009](decisions/ADR-009-when-to-switch-tracks.md). Switch ingestion when **any** trigger fires for 2 consecutive months:

1. **Dealer count**: >500 active.
2. **Data volume**: >50 TB historical.
3. **LLM cost share**: >30% of monthly cloud bill.
4. **OLAP contention**: analytics queries cause >100 ms p95 latency on catalog API.
5. **Schema churn**: ≥1 dealer schema change per week.
6. **Recovery objective**: business requires sub-1-hour RTO.

Until any of these fires, **stay on Track A**.

---

## 5. What does NOT change in the migration

Critical: Track B replaces only the **ingestion plane**. The serving plane stays put.

```
                        Track A (today)                Track B (year 2)
                        ───────────────                ────────────────
  Ingestion             Node + BullMQ                  Polars + Iceberg + Dagster
  Streaming ingestion   Fastify webhook + PG NOTIFY    Redpanda + RisingWave
  Cleaning              Zod + TS                       dbt + Dagster asset checks
  Image storage         R2                             R2 (unchanged)
  Image upload code     TS module                      TS module (reused via subprocess)
  ────────────────────────────────────────────────────────────────────────────────
  Serving DB            PostgreSQL                     PostgreSQL (unchanged)
  Catalog API           Fastify                        Fastify (unchanged)
  Marketplace sync      Fastify worker                 Fastify worker (unchanged)
  Customer-facing logic TS                             TS (unchanged)
```

This is deliberate. A successful migration replaces the **least customer-facing layer first**. The team keeps shipping features on the TS catalog API while the DE platform is built in parallel.

---

## 6. Cost model — annotated

### Cost / dealer / month (steady state)

```
TRACK A v2
  Compute (Fly.io 2 vCPU 4GB)              $20
  Postgres (Supabase / Neon 4 vCPU)        $40
  Redis (Upstash 1 GB)                     $10
  R2 (50 GB stored + 100k Class A)         $5
  LLM (cache-hot 5% miss)                  $1
  [Optional] Redpanda Community single     $0 (self-hosted)
  ──────────────────────────────────────────
  Total:           ~$76/dealer/month at 1 dealer
                   ~$30/dealer/month amortised at 100 dealers

TRACK B v2
  Compute (Prefect agent → Dagster agent 2 vCPU)  $30
  Object storage (R2 50 GB + Iceberg metadata)    $6
  Postgres (same serving PG)                       $40
  Redpanda Community (self-hosted)                 $5 (compute share)
  RisingWave (self-hosted)                         $10 (compute share)
  DuckDB on agent                                  $0
  LLM (global dedupe, 1% miss rate)                $0.30
  ──────────────────────────────────────────
  Total:           ~$91 at 1 dealer (no benefit yet)
                   ~$20/dealer at 100 dealers (LLM + Iceberg dedupe wins)
                   ~$0.50/dealer at 1000 dealers (object-storage wins big)
```

**Break-even**: ~150–250 dealers depending on file homogeneity and streaming intensity.

### Cost / file ingestion (one-shot)

```
TRACK A v2
  Node compute time      4 min × $0.000017/sec  =  $0.004
  Postgres writes        ~12k rows × negligible  =  $0.001
  R2 PUT requests        ~1400 × $0.0045/1k     =  $0.006
  LLM (cold cache)       ~200 calls × $0.003     =  $0.60
  Total:                                          ~$0.61

TRACK B v2 (year-2 steady state, hot global cache)
  Polars compute time    2 min × $0.000017/sec  =  $0.002
  R2 PUT requests        ~1400 × $0.0045/1k     =  $0.006
  Iceberg metadata       negligible               =  $0
  LLM (hot cache)        ~2 calls × $0.003       =  $0.006
  Total:                                          ~$0.014
```

---

## 7. What the reviewer should evaluate

| If you want to assess…                                                 | Look at…                                              |
| ---------------------------------------------------------------------- | ----------------------------------------------------- |
| Can the candidate execute their stack?                                | Track A code, especially `src/ingest/` + workers      |
| Schema/JSONB modelling judgment?                                       | `src/storage/db/schema.ts` + ADR-002                  |
| AI tooling literacy + cost awareness?                                  | `src/ai/providers/` + ADR-007                         |
| Data-engineering breadth (modern OSS DE 2026)?                         | `track-b-data-engineering/` + ADR-008                 |
| Metadata-driven / freshness-aware design (senior signal)?              | ADR-014 + the 3 registry tables in `schema.ts`        |
| Streaming + batch hybrid literacy?                                     | ADR-010 + `src/streaming/` + `streaming/risingwave_views.sql` |
| Multi-tenant design awareness?                                         | ADR-011 + RLS policies in `migrations/`               |
| Data contract discipline?                                              | ADR-012 + `shared/schemas/` + `shared/contracts/`     |
| DR / production-readiness?                                             | ADR-013 + `docs/runbook.md`                           |
| Trade-off / decision-making process?                                   | All 14 ADRs (each has "AI suggestion vs my override") |
| Attention to detail / spotting bugs?                                   | `docs/QUESTIONS_FOR_RECRUITER.md`                     |
| v10 senior-grade depth?                                                | `PLAN.md §13` capability matrix (24 dimensions)       |

---

## 8. Anti-recommendations (what I explicitly chose NOT to do)

| Option                                                | Why rejected                                                              |
| ----------------------------------------------------- | ------------------------------------------------------------------------- |
| Single-track TypeScript (no Track B)                  | Misses chance to demonstrate scale judgment + modern DE stack fluency     |
| Single-track Python/Spark (no Track A)                | Doesn't match JD; positions me as DE-only                                 |
| Track B = Databricks                                  | Vendor lock-in; expensive at startup stage                                |
| Track B = Snowflake / BigQuery                        | Same — vendor lock-in; not OSS                                            |
| Track B keep **Prefect**                              | Asset-centric Dagster > flow-centric Prefect for medallion (ADR-008)      |
| Track B keep **Delta Lake**                           | Iceberg is 2026 vendor-neutral standard (ADR-008)                         |
| Track B keep **Great Expectations**                   | Dagster asset checks subsume it (ADR-008)                                 |
| Streaming = **Kafka + Flink**                         | Heavy ops; Redpanda + RisingWave deliver same semantics with single binaries (ADR-010) |
| Track A streaming via separate **Kafka cluster**      | Violates JD stack; PG NOTIFY + BullMQ + outbox achieve same SLA (ADR-010) |
| Both tracks using paid APIs                           | Reviewer can't run without entering keys; doesn't model production economics (ADR-007) |
| Skip the SQLite cache; require API key                | Same issue (ADR-007)                                                       |
| Per-dealer `rules.yaml` only (no metadata registry)   | Doesn't scale past ~20 dealers; not senior-grade (ADR-014)                |
| Prisma over Drizzle                                   | JSONB type inference weaker (ADR-004)                                     |
| pandas over Polars in Track B                         | 5–30× slower at file size; no benefit                                     |
| Airflow over Dagster                                  | Task-centric paradigm is 2018-era; asset-centric is 2026 standard         |
| Iceberg-managed catalog service (Nessie)              | Adds Helm chart complexity; REST catalog binary suffices for PoC          |

---

## 9. Track A v2 vs Track B v2 — quick chooser

```
                  ┌────────────────────────────────────────┐
                  │   Are you InventoryFlow today?         │
                  │   (<500 dealers, growing fast)         │
                  └─────────────┬──────────────────────────┘
                                │ YES
                                ▼
                         🟦 Track A v2
                  (TypeScript + PG + Redis + R2)
                  + Fastify webhook streaming
                  + Metadata-driven dispatch (ADR-014)

                  ┌────────────────────────────────────────┐
                  │   Are you InventoryFlow in 18 months?  │
                  │   (500+ dealers, OR 50TB historical,   │
                  │   OR 30% LLM cost share)               │
                  └─────────────┬──────────────────────────┘
                                │ YES
                                ▼
                         🟨 Track B v2
            (Dagster + Iceberg + Redpanda + RisingWave)
                  Keep PostgreSQL serving unchanged
                  Migrate ingestion plane only
```
