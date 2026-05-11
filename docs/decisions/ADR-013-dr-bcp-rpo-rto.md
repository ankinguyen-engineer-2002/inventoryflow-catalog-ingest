# ADR-013: Disaster recovery + BCP (RPO/RTO targets)

## Status
Accepted — 2026-05-11 · scope: targets + runbook; full multi-region deferred

## Context

InventoryFlow has paying customers and growing 30%/week. A catalog corruption incident or regional outage that takes the marketplace listings offline directly translates to lost dealer revenue — and possibly suspension from eBay/Amazon (both penalize prolonged listing errors).

A senior submission must include explicit **Recovery Point Objective** (RPO — how much data loss is tolerable) and **Recovery Time Objective** (RTO — how long can the system be down). Without these, "we have backups" is theater.

## Decision

### Service tiers and targets

| Service surface                      | RPO    | RTO    | Strategy                                                       |
| ------------------------------------ | ------ | ------ | -------------------------------------------------------------- |
| Catalog API (read)                   | 0      | <5 min | Postgres replica + automatic failover                          |
| Catalog API (write)                  | 5 min  | <15 min | Postgres PITR + WAL streaming to standby                       |
| R2 image bucket                      | 0      | <1 min | R2 is multi-region by default                                  |
| Ingest pipeline (Track A)            | 1 hour | <1 hour | Re-ingest from last good `ingest_runs.run_id`                 |
| Ingest pipeline (Track B)            | 5 min  | <30 min | Iceberg `VERSION AS OF` + Dagster backfill                    |
| Streaming events (Track A LISTEN/NOTIFY) | 0  | <2 min | `stream_events` table replays from `stream_outbox`             |
| Streaming events (Track B Redpanda)  | 0      | <2 min | Redpanda retention + RisingWave checkpoint                     |
| LLM cache                            | 24 h   | <30 min | Committed in repo; regenerable from xlsx + handoff             |

### Backup strategy

```
DAILY:    pg_dump → S3 bucket (encrypted, 30-day retention)
HOURLY:   WAL archiving → S3 (point-in-time recovery, 7-day window)
REALTIME: Logical replication slot → standby Postgres
WEEKLY:   Iceberg snapshot listing → archive bucket (90-day retention)
CONTINUOUS: R2 → R2 cross-region (built-in)
```

### Failure scenarios + responses

| Scenario                                          | Response                                                              | Time |
| ------------------------------------------------- | --------------------------------------------------------------------- | ---- |
| Postgres primary fails                            | Promote replica via pg_auto_failover; DNS swap                        | <5 min |
| One dealer's catalog corrupted by buggy run       | `SELECT … FROM products WHERE source_run_id = $bad_run_id` audit, then re-run ingest for that dealer | <30 min |
| Cross-tenant data leak suspected                  | `ALTER USER api_user SET row_security = ON` ensures RLS enforced; audit `ingest_audit` for queries | <1 hour |
| All Iceberg gold tables wrong after bad dbt deploy | Iceberg `VERSION AS OF` rollback all 3 gold tables; Dagster re-materialize | <30 min |
| Region outage (R2 primary)                        | R2 auto-fails-over (built-in); no action needed                       | <1 min |
| LLM cache corrupted                               | `rm shared/llm-cache.sqlite`, regenerate via handoff or batch         | <30 min |
| Webhook flood (10x normal)                        | BullMQ rate-limit kicks in; excess queued; SLA degrades to <5s p95   | Continuous |
| Bad ingestion code deployed                       | Roll back deploy; re-ingest affected dealers from `ingest_runs` history | <1 hour |
| Total disaster (region + DB + backups)            | LAST RESORT: re-ingest from raw landing bucket (xlsx still on R2 raw) | Hours-days |

### Backup verification

Backups that aren't periodically restored are theater. The DR runbook includes a **quarterly restore drill**:

1. Pick a random `ingest_runs.run_id` from 30 days ago.
2. Restore Postgres to a sandbox from that point in time.
3. Verify `SELECT COUNT(*) FROM products` matches the recorded `ingest_runs.rows_succeeded`.
4. Document any drift in `docs/runbook.md` lessons-learned.

### Incident response

`docs/runbook.md` has the on-call runbook with:
- Severity levels (SEV-1 catalog-API down; SEV-2 ingestion paused; SEV-3 latency degradation)
- Escalation matrix
- Decision tree for "should I roll back?"
- Post-mortem template

## What's in PoC

| Element                              | PoC status                                |
| ------------------------------------ | ----------------------------------------- |
| RPO/RTO targets documented           | ✅ This ADR + runbook                     |
| Postgres backup strategy documented  | ✅ pg_dump + WAL archive scripts in `track-a-jd-native/scripts/` |
| Postgres replica setup               | 📋 Deferred — single-node PG in PoC       |
| Iceberg time travel demonstrated     | ✅ One sample query in `track-b-data-engineering/notebooks/` |
| LLM cache regeneration documented    | ✅ In runbook (ADR-007)                   |
| Quarterly restore drill              | 📋 Process documented; first drill post-deploy |
| Multi-region                         | 📋 Deferred — single-region PoC           |
| Chaos engineering                    | 📋 Deferred — single-engineer team        |

## AI suggestion vs my override

**Claude initially suggested** skipping a formal DR ADR ("backups are mentioned in the runbook").

**I overrode** because:

1. **RPO/RTO targets are the difference** between "we hope backups work" and "we know data loss is bounded by X minutes." Senior signal.
2. **The JD describes a 30%/week-growth business** with paying customers — DR isn't optional at that velocity.
3. **Marketplace platforms penalize unavailability**. eBay's Top Rated Seller program docks accounts that miss SLAs. The downstream impact is real.
4. **Documenting the gap honestly** ("multi-region deferred until X dealers") is more credible than implementing half a multi-region story.

## Trade-offs accepted

- **No multi-region in PoC** — acceptable until first enterprise dealer demands it.
- **Quarterly drill is honor-system** until automated — first automated drill is a post-PoC investment.
- **No active-active** for write path — single primary Postgres. Acceptable at scale; revisit at 10k+ writes/sec.
- **R2 cross-region is provider-managed** — we trust Cloudflare; documented in risk register.

## When to revisit

- **First SEV-1 incident**: post-mortem will likely identify gaps in this matrix.
- **First enterprise dealer with contracted SLA**: tighten RTO numbers per their contract.
- **Geographic expansion** (e.g., EU dealers): multi-region becomes a regulatory requirement (GDPR data residency), not just an SLA improvement.

## Sources

- Postgres PITR docs: https://www.postgresql.org/docs/16/continuous-archiving.html (retrieved 2026-05-11). [Verified]
- pg_auto_failover: https://github.com/citusdata/pg_auto_failover (retrieved 2026-05-11). [Verified]
- Apache Iceberg time travel: https://iceberg.apache.org/docs/latest/spark-queries/#time-travel (retrieved 2026-05-11). [Verified]
- Cloudflare R2 multi-region: https://developers.cloudflare.com/r2/reference/data-location/ (retrieved 2026-05-11). [Verified]
- "Establishing SLOs at Datadog" (RPO/RTO best practices): https://www.datadoghq.com/blog/establishing-service-level-objectives/ (retrieved 2026-05-11). [Verified]
- Google SRE Book Ch. 4 (Service Level Objectives): https://sre.google/sre-book/service-level-objectives/ (retrieved 2026-05-11). [Verified]
