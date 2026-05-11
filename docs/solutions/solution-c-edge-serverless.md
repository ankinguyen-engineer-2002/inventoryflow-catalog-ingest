# Solution C — Edge-First Serverless

> **Status:** Architecture-only. Not implemented in this submission.
> **Positioning:** Highest-availability, lowest-latency reads. Worth exploring when dealer catalogs become consumer-facing (search-engine-indexable).

---

## Premise

Solutions A (TypeScript control plane) and B (Polars + Iceberg lakehouse) optimise for **operational simplicity** and **analytical depth** respectively. Solution C optimises for **read latency at global scale** by pushing the catalog data plane all the way to the edge.

This is the architecture you adopt when:

- Catalog browsing has to feel instant from any country
- SEO-driven traffic (Google Shopping crawlers) demands sub-50 ms TTFB worldwide
- Write throughput is moderate (under 10k writes per second sustained)
- The team is comfortable with the Cloudflare developer platform

---

## Stack

| Layer                   | Choice                              | Why                                                                |
| ----------------------- | ----------------------------------- | ------------------------------------------------------------------ |
| Compute (read path)     | Cloudflare Workers                   | 300+ edge locations; cold-start under 5 ms                         |
| Compute (write path)    | Cloudflare Containers / Workflows    | Long-running ingest jobs in a regional container                   |
| Catalog database        | Cloudflare D1 (SQLite at edge) + R2  | Replicated to every region; native to Workers                      |
| Object storage          | Cloudflare R2                        | Zero egress fee; binary images served direct                       |
| Cache                   | Workers KV + Cache API               | Per-region key-value cache; 60-second invalidation TTL             |
| Streaming               | Cloudflare Queues + Durable Objects  | Per-dealer event ordering via Durable Object actor model           |
| Analytics               | Cloudflare Workers Analytics Engine  | OLAP queries over event data with sub-second response              |
| AI                      | Workers AI (Llama / Qwen models)     | Edge-local inference; zero data egress                             |
| Deployment              | Wrangler + GitHub Actions            | Declarative; one config controls global deploy                     |

---

## Architecture

```
                 ┌────────────────────────────────────────────────────────────┐
                 │  Global users (eBay/Amazon crawlers, dealer mobile apps)   │
                 └──────────────────────────────┬─────────────────────────────┘
                                                │
                              ┌─────────────────▼──────────────────┐
                              │  Cloudflare anycast network        │
                              │  (300+ edge locations)             │
                              └─────────────────┬──────────────────┘
                                                │
                              ┌─────────────────▼──────────────────┐
                              │  Read path — runs at the EDGE      │
                              │                                    │
                              │  Workers (TypeScript)              │
                              │   ├─ Cache API check (1ms)         │
                              │   ├─ Workers KV lookup (5ms)       │
                              │   ├─ D1 SQL query (10-30ms)        │
                              │   └─ Stream image from R2          │
                              │                                    │
                              │  TTFB target: under 50ms globally  │
                              └─────────────────┬──────────────────┘
                                                │ (cache miss only)
                              ┌─────────────────▼──────────────────┐
                              │  Write path — runs in ONE region   │
                              │                                    │
                              │  Cloudflare Workflows              │
                              │   ├─ Ingest xlsx from R2           │
                              │   ├─ Parse via TypeScript          │
                              │   ├─ Upsert to D1 primary          │
                              │   ├─ Publish to Queues             │
                              │   └─ Workers AI for translations   │
                              │                                    │
                              │  D1 replication: automatic         │
                              │  to every edge region              │
                              └─────────────────┬──────────────────┘
                                                │
                              ┌─────────────────▼──────────────────┐
                              │  Streaming — Durable Objects       │
                              │  + Queues                          │
                              │                                    │
                              │  One Durable Object per dealer:    │
                              │   ├─ Event ordering guaranteed     │
                              │   ├─ Per-tenant state isolation    │
                              │   └─ Coalesce stock updates        │
                              └────────────────────────────────────┘
```

---

## What you gain

| Property                        | Solution A          | Solution C                      |
| ------------------------------- | ------------------- | ------------------------------- |
| Read latency (global, p95)      | 200–500 ms          | **30–50 ms**                     |
| Write throughput                | 5k/sec single PG    | 10k/sec D1 primary               |
| Infrastructure cost (1k dealers)| ~$1500/month        | **~$200/month** (Workers free tier covers most reads) |
| Operational burden              | Database upgrades, backups | Cloudflare handles it     |
| Vendor lock-in                  | Low                 | **High** (Cloudflare-specific)   |
| Cold-start latency              | N/A (always running)| Under 5 ms                       |
| LLM cost (edge inference)       | Per-call API spend  | Workers AI flat-rate             |

---

## What you give up

- **Cloudflare lock-in.** D1, Workers, Durable Objects, Queues are not portable. Migrating away requires rewriting the data plane.
- **SQL feature set.** D1 is SQLite — no `jsonb_path_ops`, no GIN, no `LISTEN/NOTIFY`. Fitment query would use SQLite JSON1 functions (slower) or a denormalised lookup table.
- **OLAP depth.** Analytics Engine handles ingestion-time metrics well; complex historical queries still want a real warehouse.
- **D1 size limit.** 10 GB per database. Multi-tenant deploys shard by `dealer_id` across multiple D1 databases.
- **No RLS.** Multi-tenancy enforced at the Worker code layer, not the database.

---

## When to choose Solution C

Adopt this when **two or more** of these hold:

1. Global catalog browse is a customer-facing product (not just dealer internal)
2. The business is willing to commit to Cloudflare as the platform
3. Read traffic outweighs writes by 100:1 or more
4. SEO / Core Web Vitals are revenue-impacting metrics
5. The team has Cloudflare Workers production experience

If none of the above, Solution A is simpler and Solution B scales further analytically.

---

## Implementation budget (AI-assisted estimate)

- Read-path Worker + D1 schema: ~4 hours
- Write-path Workflow with ingest port from Solution A: ~6 hours
- Durable Objects for streaming: ~4 hours
- Workers AI integration for translations: ~2 hours
- Wrangler config + CI/CD: ~2 hours

**Total: ~18 hours AI-assisted, or ~5 days manual.**
