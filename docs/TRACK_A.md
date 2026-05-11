# Track A — Engineering Write-up

Single document narrating what was built, why each non-obvious decision was made, what broke along the way, and what's deliberately deferred. Read after `PLAN.md` for the strategic framing.

---

## 0. What this is, and what it isn't

This is a take-home submission for the InventoryFlow Senior Engineer role: ingest one OEM Excel file (241 MB, 110 sheets, 1,586 embedded schematic images, English+Chinese cells) into a clean PostgreSQL catalog with R2-hosted schematic images and a JSONB fitment column.

**What it is:**

- A working end-to-end ingestion pipeline that turns the raw xlsx into 3,938 rows in `products`, 382 distinct R2 objects, and 11,098 product↔image associations on every run.
- An HTTP surface (Fastify) with batch and near-realtime streaming endpoints.
- An `ILLMProvider` abstraction with five concrete implementations and a JSONL cache committed to the repo so the reviewer never pays an LLM bill.
- 12 normalised tables behind a generated `part_number_norm` unique index that is `NULLS NOT DISTINCT` so re-runs are upserts, not duplicates.

**What it isn't:**

- A deployed product. The submission is source code only. The same code runs against MinIO locally and against Cloudflare R2 in production by changing three env vars.
- A finished schema for every dealer. The current detector handles three header signatures observed in the Kayo sample (`chassis`, `engine`, `chassis_u8`). The metadata-driven control plane (ADR-014) is the bookmark for accommodating dealer N+1 without a code deploy.
- Track B. Track B is the documented migration target in `PLAN.md §5` and `docs/COMPARISON.md`; the scaffold exists, the implementation does not.

---

## 1. The data, as it actually appeared

I parsed the file by hand before writing any code. That step is the most important thing I did, and the rest of the architecture follows from what fell out of it.

### 1.1 The 10 mess patterns

| #  | Pattern                                                              | Why it matters                                                                  |
| -- | -------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| 1  | Multi-section sheets: header row repeats 10–20× per sheet            | Cannot iterate from row N. Section boundary is the header signature itself.     |
| 2  | Three distinct header signatures, not one (`chassis`, `engine`, `chassis_u8`) | A "Part Number" parser silently mis-ingests half the file.              |
| 3  | `No.` column is polymorphic: `1.0`, `"1-1"`, `"1-6L"`, `null`        | These are not duplicates. They are sub-assemblies, left/right variants, and unnumbered parts. Dedupe by Part Number, not by callout. |
| 4  | Same `No.` with different Part Numbers = SKU variant by date/colour  | Keep both rows. `"black grip"` vs `"black grip(9.26.2022~)"`.                   |
| 5  | CN cells return as `{ richText: [{text}, …] }`, not strings, in exceljs | A naive `String(v)` produces `"[object Object]"`. Must walk the rich-text array. |
| 6  | 1,586 schematic images anchored via `xl/drawings/drawingN.xml`       | `exceljs` doesn't expose this. Must open the zip and parse the drawing XML.    |
| 7  | Fitment (year, model code, variant) is in the **sheet name**         | `"PREDATOR 125 (2016-2020)"`, `"AU180-2 (2023+) Parts Diagram"`, `"2024+ TT125 EPA"`. Regex parser, seven patterns. |
| 8  | `make = "Kayo"` is nowhere in the data                               | Cannot be inferred from any cell. The metadata-driven control plane (ADR-014) is where `dealers.inferred_make` lives in production. |
| 9  | Sheet names with trailing whitespace                                 | `"FOXStorm 70 AY70-2 "`. Normalise on key, preserve on display.                 |
| 10 | ~12 "exception" sheets with completely different schemas             | Carburetor Jets, Spark Plugs, Wheel specs. Out of scope for primary catalog; routed to a separate `reference_specs` path. |

Each pattern is a concrete commit in the history. I called out the ones that surprised me in `docs/QUESTIONS_FOR_RECRUITER.md`.

### 1.2 Why these matter

The data is "messy" the way real OEM exports are: not corrupted, but inconsistent at every layer. Cell types are runtime-polymorphic, the structure mixes "table" with "diagram", and the semantics of identity (what makes two rows the same product) is contextual. A parser that doesn't enumerate these is going to ingest the file and silently produce wrong output. Counts, fitment lookups, and downstream sync to marketplaces will all look fine in spot checks and fail in production.

This is why §2 starts with the validator, not the parser.

---

## 2. Architecture — five planes

```
                       ┌─────────────────────────────────────────────────────┐
                       │  ⓪ INGRESS                                          │
                       │                                                     │
                       │  CLI:   pnpm ingest <xlsx>        (batch)           │
                       │         pnpm enrich --mode audit  (LLM cross-check) │
                       │  HTTP:  POST /runs                (batch enqueue)   │
                       │         POST /events/{inventory,pricing,order}      │
                       │         GET  /healthz /readyz /metrics              │
                       └────────────────────────┬────────────────────────────┘
                                                │
                       ┌────────────────────────▼────────────────────────────┐
                       │  ① CONTROL PLANE                                    │
                       │                                                     │
                       │  • ingest_runs       — UUID per run, status, cost   │
                       │  • stream_events     — UUID per webhook, outbox     │
                       │  • dealer_pattern_   — metadata-driven dispatch     │
                       │    bindings            (ADR-014, sketched)          │
                       │  • BullMQ queues     — parse-file / sheet / image / │
                       │                        enrich-llm / stream-*        │
                       │  • Rate limiter      — Redis token-bucket for R2    │
                       │  • Multitenant plugin — x-dealer-id → req.dealerId  │
                       └─────┬────────────────┬──────────────────┬───────────┘
                             │                │                  │
                ┌────────────▼────┐ ┌─────────▼──────────┐ ┌─────▼────────────┐
                │ ② DATA PLANE    │ │ ③ INTELLIGENCE      │ │ ④ STORAGE PLANE  │
                │                 │ │    PLANE (AI)       │ │                  │
                │ Batch workers   │ │                     │ │ • PostgreSQL 16  │
                │  (conc=8):      │ │  ILLMProvider:      │ │   12 tables      │
                │   parse-file    │ │   ├ mock             │ │   GIN(fitment   │
                │   parse-sheet   │ │   ├ cached(decorate)│ │     jsonb_path_  │
                │   upload-image  │ │   ├ claude-code-    │ │     ops)         │
                │   enrich-llm    │ │   │  handoff         │ │   GIN trgm name │
                │                 │ │   ├ ollama (local)  │ │                  │
                │ Stream workers  │ │   ├ anthropic-batch │ │ • Cloudflare R2  │
                │  (conc=32):     │ │   └ gemini (stub)   │ │   (MinIO local)  │
                │   stream-       │ │                     │ │   SHA-256 keyed  │
                │   {inventory,   │ │  JSONL cache file   │ │                  │
                │    pricing,     │ │  (committed in repo)│ │ • Redis 7        │
                │    order}       │ │                     │ │   BullMQ + LIS-  │
                │                 │ │  Audit: every call  │ │   TEN/NOTIFY bus │
                │ DLQ + retry     │ │  logged to          │ │                  │
                │ + outbox        │ │  ingest_audit table │ │                  │
                └─────────┬───────┘ └──────────┬──────────┘ └──────────┬──────┘
                          │                    │                       │
                          └────────────────────┴───────────────────────┘
                                               │
                       ┌───────────────────────▼──────────────────────────────┐
                       │  ⑤ OBSERVABILITY PLANE                               │
                       │                                                      │
                       │  • Pino structured logs, run_id correlation          │
                       │  • Prometheus /metrics endpoint                      │
                       │  • OpenTelemetry tracing (one trace per run/event)   │
                       │  • ingest_audit  — every LLM call: prompt hash,      │
                       │                    response, tokens in/out, cost,   │
                       │                    latency, cache_hit                │
                       │  • Lineage: row → section → sheet → file → run       │
                       └──────────────────────────────────────────────────────┘
```

The five planes are not invented; they're a generalisation of what every production data platform converges on (Confluent Cloud, Databricks, Airflow Cloud). I'm using the same names so anyone reading the code can map the structure to their existing mental model.

What I gain from this layout:

- **Failures don't cross planes.** A bad sheet doesn't crash the API. A stuck stream event doesn't block batch ingestion. A bad LLM response is caught by the cache decorator without touching the database.
- **Test surfaces are narrow.** I can unit-test `section-detector` without booting Postgres because it doesn't know about Postgres. The same goes for `r2-uploader`, `ILLMProvider`, etc.
- **Operational levers exist.** Concurrency is per-queue. Rate limits are per-upstream. Cache is its own file. Each is an independent dial.

---

## 3. The hard parts — bugs caught, decisions made

Most of these don't appear in the public commit messages. They were the friction in getting the system to "obviously work."

### 3.1 RichText cells silently corrupting CN names

**Symptom:** First end-to-end run inserted `name_cn = "[object Object]"` for every product.

**Diagnosis:** exceljs returns CN cells as `{ richText: [{ text: "把套" }, ...] }` because the cell has explicit font formatting. `String({richText: [...]})` is `"[object Object]"`.

**Fix:** `src/ingest/cell-utils.ts` walks the rich-text array and concatenates `.text` values. The same util is used by both the section detector (so header matching survives rich-text headers) and the row normaliser. Two callers, one source of truth.

This is why I added the `cell-utils` module instead of inlining the coercion in each parser. The bug appeared once but the underlying mismatch (exceljs runtime shape ≠ what a `CellValue` type suggests) recurs at every cell read.

### 3.2 Sheets 5+ silently producing zero sections

**Symptom:** Full-file ingest reported 1,758 attempted rows out of an estimated 27,000+. Most sheets had `sectionsDetected: 0`.

**Diagnosis:** Header rows in `AT110 EPA` and similar sheets use a different schema entirely. Instead of `Part Number`, they have `U8 Code` + `Model`. My signature list had two entries; the data has three.

**Fix:** Added a third `HeaderSignature` with `kind: "chassis_u8"`, marked `U8 Code` as its `partNumberColumn`, and pushed the per-section "which column holds the part number" logic into the signature itself rather than branching in `row-normalizer`. Section detector tries every known signature; first that satisfies its `required` set wins.

**Lesson:** Bake the variant into the data structure, not the control flow. Adding signature #4 next year is a YAML edit, not a code change.

### 3.3 Idempotency broken: re-run produced 22,196 rows

**Symptom:** First run: 11,098 products. Second run: 22,196 — exactly doubled.

**Diagnosis:** PostgreSQL's default `UNIQUE (part_number_norm, source_dealer_id)` treats two `NULL` dealer_ids as distinct. Every re-run for an un-tenanted ingest re-inserts every row.

**Fix:** Hand-written migration `0001_nulls_not_distinct.sql` rebuilds the index with `NULLS NOT DISTINCT`. Drizzle Kit 0.30 doesn't model the modifier; schema.ts has a comment pointing at the migration so a future maintainer doesn't generate a conflicting one.

This is the kind of bug that ships to production and shows up months later as "why does the catalog have 50× more rows than parts?". Worth the rebuild.

### 3.4 BullMQ refused queue names containing `:`

A trivial one — but I had named queues `q:parse-file` in PLAN.md before checking. BullMQ uses `:` as its internal Redis key separator. Renaming was a one-character commit, but the principle: name conventions in code, not in plans.

### 3.5 Redis `allkeys-lru` silently drops BullMQ jobs

**Symptom:** Workers connected, queues had jobs, nothing got processed.

**Diagnosis:** I'd configured Redis with `maxmemory-policy allkeys-lru` to be a good citizen under memory pressure. BullMQ refuses this — it requires `noeviction` so it can guarantee job persistence. Without `noeviction`, Redis quietly evicts under pressure and jobs vanish.

**Fix:** `noeviction` in `docker-compose.yml`, with an explanatory comment so a future maintainer doesn't "optimise" it back.

### 3.6 `pg_notify` parameter type inference

**Symptom:** Worker crashed with `PostgresError: could not determine data type of parameter $1`.

**Diagnosis:** `postgres.js` tagged template `sql\`SELECT pg_notify('ch', ${payload})\`` doesn't tag `$1` with a type. `pg_notify(text, text)` is ambiguous because the second arg could be many things.

**Fix:** `sql.unsafe(\`SELECT pg_notify('inventory_change', $1::text)\`, [payload])`. Explicit cast at the SQL boundary.

---

## 4. LLM integration — how it actually works

The test PDF says: *"AI Tooling: We highly encourage the use of AI coding tools (Cursor, Windsurf, Copilot) or Vision LLMs (OpenAI, Claude) to parse the messy PDF and map the data."* Implementing this in a way that survives both (a) the reviewer running it for free and (b) production scaling to 1000 dealers was the most architecturally interesting part of the work.

### 4.1 Constraints

1. The reviewer should not need to enter an API key.
2. I (the candidate) am not buying API credits for this submission.
3. The same code must be production-viable without rewrites.

These three are usually in tension; the resolution is the provider abstraction plus a cache decorator that fronts every call.

### 4.2 Shape of the abstraction

```
                              ┌────────────────────────────────────┐
                              │  Pipeline code only sees:          │
                              │                                    │
                              │  const llm = createLLMProvider();  │
                              │  const r = await llm.enrich({...});│
                              │                                    │
                              │  r.result, r.confidence,           │
                              │  r.meta.cost, r.meta.cacheHit      │
                              └─────────────────┬──────────────────┘
                                                │
                                                ▼
                              ┌────────────────────────────────────┐
                              │  CachedLLMProvider                 │
                              │  (always-on decorator)             │
                              │                                    │
                              │  • Cache key: SHA-256(field +      │
                              │    sorted inputs)                  │
                              │  • Backend: JSONL file in repo     │
                              │  • Hit  → return cached ($0, <1ms) │
                              │  • Miss → call upstream + write    │
                              │  • Never caches null (handoff      │
                              │    "task pending" sentinel)        │
                              └─────────────────┬──────────────────┘
                                                │
                          ┌─────────────────────┴─────────────────────┐
                          │ Upstream picked by env.LLM_PROVIDER:      │
                          ▼                                           ▼
              ┌──────────────────────┐                  ┌──────────────────────┐
              │  DEV / TEST           │                  │  PRODUCTION           │
              │  ────────             │                  │  ──────────           │
              │  mock                 │                  │  ollama   (self-     │
              │    fixtures, tests    │                  │           hosted, $0) │
              │  cached               │                  │  anthropic-batch     │
              │    default for        │                  │    (cloud, paid,     │
              │    reviewer           │                  │     ~$5/1000 dealers │
              │  claude-code-handoff  │                  │     /month at scale) │
              │    one-time seeding   │                  │  gemini (stubbed:    │
              │    via my Claude      │                  │    TOS data-training │
              │    Max session        │                  │    risk for prod)    │
              └──────────────────────┘                  └──────────────────────┘
```

The cache key uses sorted-key JSON because plain `JSON.stringify` is insertion-order-dependent. The first version of this had a bug where the same input produced different cache keys across runs; the second version sorts `inputs` keys explicitly. There's a unit test that asserts this.

### 4.3 Lifecycle (three phases)

```
Phase 1 — SEEDING (one-time, dev only)
─────────────────────────────────────
  LLM_PROVIDER=claude-code-handoff pnpm enrich --mode audit --limit 60

  Pipeline emits shared/handoff/translation_tasks.jsonl (one task per row).
  I open Claude Code, read the file, translate each CN string by hand, and
  write shared/handoff/translation_results.jsonl. Re-run with the same env
  setting — the handoff provider now finds results and returns them.
  CachedLLMProvider persists each response into shared/llm-cache.jsonl.

  This is the only phase that requires me. It's idempotent: deleting the
  cache and re-running reproduces the same file.

Phase 2 — REVIEWER (zero-cost)
─────────────────────────────────────
  LLM_PROVIDER=cached pnpm enrich --mode audit --limit 60    # default

  Every request hits the committed cache. Zero upstream calls.
  ingest_audit shows cache_hit=true for every row.

Phase 3 — PRODUCTION (autonomous)
─────────────────────────────────────
  LLM_PROVIDER=ollama         OLLAMA_MODEL=qwen2.5:7b pnpm enrich
  LLM_PROVIDER=anthropic      ANTHROPIC_API_KEY=...    pnpm enrich

  No human in the loop. Cache hit rate ~99% at steady state because the
  same CN string appears in many dealers' files; one translate call serves
  them all globally.
```

### 4.4 Audit mode — what LLM is actually used *for*

The data already had English names in most cells. So "translate CN to EN" wasn't a fill-the-blank problem; it was a verification problem. `enrich --mode audit` is what runs:

```
For each (cn, current_en) pair:
  llm_en   = LLM.translate(cn)                       # via ILLMProvider
  consensus = jaccard_token_set(current_en, llm_en)  # cheap, deterministic

  agree    (≥0.5)   → LLM confirms data
  partial  (≥0.2)   → LLM adds detail (data correct but terse)
  disagree (<0.2)   → likely error in data; needs human review

  UPDATE products SET data_quality = data_quality || {
    translation_verified:           true,
    translation_verified_by:        provider_name,
    translation_consensus:          label,
    translation_consensus_score:    score,
    translation_llm_alt:            llm_en,
    translation_confidence:         response.confidence
  }
```

This is "Layer 3" of the five-layer accuracy framework I outlined in ADR-007 (confidence scoring, rule validation, cross-row consistency, ensemble agreement, production feedback loop). It's the layer that demonstrates concrete value because it produces actionable output: a list of rows where the OEM-supplied translation disagrees with a fresh LLM translation, scored.

### 4.5 What the audit found

68 products audited, 16% flagged for review:

| Status   | Count | What it means                                                    |
| -------- | ----- | ---------------------------------------------------------------- |
| agree    | 25    | LLM confirms the existing EN name                                |
| partial  | 32    | EN is correct but LLM adds precision (model code, spec details)  |
| disagree | 11    | The current EN looks wrong against a fresh translation           |

Sample disagreements:

| CN              | Current EN            | LLM EN                                          | Issue                          |
| --------------- | --------------------- | ------------------------------------------------ | ------------------------------ |
| 转向冶金衬套    | "busher"              | steering column sintered bushing                | typo + missing context         |
| 前左右减震      | "front fork"          | front left and right shock absorbers            | wrong part type entirely       |
| 平垫 GB97.1-85  | "flat gasket"         | flat washer GB97.1-85                            | gasket ≠ washer                |
| 前碟刹驻车手柄  | "front park lock kit" | front parking brake handle                       | "kit" is the wrong noun        |

These are real defects in the dealer-supplied data. The LLM pass catches them at no marginal cost because the same cache that serves the reviewer also serves production. The dealer-supplied EN is preserved in `products.name_en`; the LLM alternative lives in `data_quality.translation_llm_alt` so a downstream review UI (or batch job) can promote the LLM value after human approval, not before.

### 4.6 Cost economics at scale

The reason this isn't an academic exercise: at 1000 dealers, this matters.

```
Steady-state monthly assumptions (1000 dealers):
  • Distinct CN strings globally:                ~50,000
  • Cache hit rate (cross-dealer, same parts):   ~99%
  • Real upstream calls per month:                ~500
  • Anthropic Sonnet pricing (Messages, 2026):    $3 / 1M input tok
                                                  $15 / 1M output tok
  • Average call size:                             80 in / 20 out
  • Per call cost:                                 ~$0.0005
  • Monthly LLM bill:                              ~$0.25

  Round up for retries, prompt drift, batch overhead: ~$1–5/month total
  at 1000 dealers.
```

The cache is the lever that makes paid APIs viable at scale. The provider abstraction is the lever that lets us choose between Ollama (self-hosted, zero variable cost, slightly lower quality) and Anthropic (cloud, paid, higher quality) without rewriting any business code.

---

## 5. Database schema — twelve tables, grouped

The schema isn't pretty but it's load-bearing. Every table answers a question someone has asked of a real catalog system.

### Catalog core (5 tables)

```
products                  ── The canonical row. Generated `part_number_norm`
                              column for case-insensitive uniqueness. JSONB
                              fitment with GIN(jsonb_path_ops). data_quality
                              JSONB for provenance + LLM audit fields.

product_images            ── m:n between products and SHA-256-keyed R2 objects.
                              Same image referenced by many products = one row
                              in object storage, many rows here.

part_number_aliases       ── Engine sheets carry OLD/NEW part numbers when the
                              OEM renames. We persist both as queryable
                              aliases so dealer systems still holding the old
                              number get a hit.

vehicle_models            ── Normalised dimension. Useful for analytics joins
                              and as a DQ target ("does fitment.model_code
                              exist in vehicle_models?").

reference_specs           ── Spark plugs, carburetor jets, wheel specs — the
                              ~12 sheets whose schema doesn't fit the
                              parts-catalog mould. Kept as opaque JSONB
                              attributes; not part of the primary catalog.
```

### Runs + audit (2 tables)

```
ingest_runs               ── One row per ingest invocation. UUID PK. status,
                              rows_attempted/succeeded/failed, llm_calls,
                              llm_cost_usd, start/finish, error. The run_id
                              is the correlation key for every log line in
                              the system.

ingest_audit              ── One row per LLM call (and any other audited
                              external API). Captures prompt hash, template
                              version, response, tokens in/out, cost,
                              latency, cache_hit. This is how we'd answer
                              "where did this product.name_en come from?"
                              months from now.
```

### Streaming (2 tables — ADR-010)

```
stream_events             ── Inbound webhook payloads, dealer-tenant scoped.
                              status: PENDING | PROCESSED | FAILED.

stream_outbox             ── The transactional-outbox pattern. We write to
                              business tables AND to outbox in the same
                              database transaction; a publisher drains
                              outbox to Redpanda / a message bus with
                              at-least-once semantics. No XA, no drift.
```

### Metadata-driven control plane (3 tables — ADR-014)

```
dealers                   ── Tenants. id, name, status, inferred_make,
                              tier, metadata JSONB.

ingestion_patterns        ── Handler registry. pattern_name (e.g.
                              "xlsx_oem_catalog_v1") → handler_module +
                              schema_signature + validation_rules +
                              default_schedule + default_freshness_sla.
                              Adding a new dealer schema is a row insert,
                              not a code change.

dealer_pattern_bindings   ── Which patterns are bound to which dealers,
                              with per-binding overrides for schedule and
                              freshness. Cron-smart skip via
                              last_run_sha256 ("source unchanged → skip
                              this cycle, still log it").
```

The MDCP tables are populated by scaffold data but not yet driven by a runtime dispatcher. The bookmark is "before the second dealer onboards, wire `dealer_pattern_bindings` into a control loop." Documented in `ADR-014`.

---

## 6. Trade-offs I accepted, and why

| Trade-off                                                              | Why                                                                              | When this becomes the wrong choice                       |
| ---------------------------------------------------------------------- | -------------------------------------------------------------------------------- | -------------------------------------------------------- |
| JSONL cache instead of SQLite                                          | better-sqlite3 needs `node-gyp` + Xcode CLT to build; `node:sqlite` requires `--experimental-sqlite` which Vite can't bundle. JSONL has zero native deps and scales to 10k entries fine. | >100k cache entries: re-eval — index lookups become O(n) on load.                |
| Drizzle ORM over Prisma                                                | Typed JSONB inference is concrete (`$type<FitmentEntry[]>()`); zero runtime; no `prisma-client-engine` binary in deploy. | If the team grows past 5 backend engineers and Prisma's ergonomics outweigh runtime cost. |
| MinIO locally, R2-shaped code                                          | `@aws-sdk/client-s3` speaks both. Swap = 3 env vars. No "Cloudflare account required to run" gate for the reviewer. | If R2 introduces non-S3 features (event notifications) we need. |
| `claude-code-handoff` for seeding the cache                            | I pay $0 in API spend; the reviewer pays $0; production swaps to anthropic-batch by env var. | If the cache regenerates frequently — bookmark to wire ollama. |
| 30 s wall-time on 241 MB, single-process                               | The JD writes "<500 dealers"; that's the target. BullMQ workers scale horizontally when needed. | At 100 dealers × 1 GB files: shard parse-sheet workers across instances. |
| `NULLS NOT DISTINCT` on the products unique index                      | Idempotency for un-tenanted runs (CI, fixtures, first-dealer scenarios). | Never. This is a strict improvement over default.       |
| Section "title" heuristic returns `null` when uncertain                 | A wrong title in `products.data_quality.section` is worse than a missing one.   | When recall on section labels matters — bookmark to add LLM-assisted titling. |
| RLS policies designed (ADR-011) but not enabled in migration           | RLS requires every connection to `SET LOCAL app.current_dealer_id` — incompatible with the current fixture flow. | When the first multi-tenant production deploy lands.    |
| Twelve "exception" sheets routed to `reference_specs` but not parsed    | They don't share the catalog schema; they need their own ingestion path. | When a dealer asks "where are the spark plug equivalencies?" — bookmark exists. |

The pattern in all of these: pick the option that's correct for *current scale*, document the trigger for the next option, and leave the seam where the swap happens.

---

## 7. What I'd do at 100× scale (Track B)

Track B is documented in `PLAN.md §5` and `docs/COMPARISON.md`. The short version: ingest moves from BullMQ workers to Dagster assets writing into Apache Iceberg on object storage, with dbt-core materialising silver/gold layers. PostgreSQL stays as the serving layer; only ingestion moves. ADR-009 spells out the six measurable triggers for the migration (>500 dealers, >50 TB historical, >30% LLM cost share, OLAP-OLTP contention, schema churn rate, RTO requirement).

Track B is not implemented in this submission. The scaffold directories exist, but I made the call to ship Track A end-to-end with LLM and streaming wired in, rather than a half-implementation of both tracks. The COMPARISON document quantifies the 18 dimensions across which the two tracks differ so the decision to switch is observable, not subjective.

---

## 8. What you'd see if you reviewed this in a year

A few things I'd want a future me to remember:

1. **The data is the spec.** The first commit was a `probe.py` that read 5 sheets and dumped them. The architecture is the negative space around the patterns it found. If a new dealer schema arrives, run the probe on it first.

2. **The cache file is a load-bearing artifact, not an output.** Treat it like a fixture: commit it, version it via `prompt_template_ver`, and have a story for regenerating it. The `shared/handoff/*` files are the audit trail of how the current cache was seeded.

3. **The 5-plane diagram in §2 isn't decoration.** Every PR should fit within one plane or explicitly cross a boundary; if "I had to change three planes to add this feature" is the answer, the abstraction broke somewhere.

4. **`NULLS NOT DISTINCT` is not the default.** Future migrations against this schema should keep it.

5. **The LLM is a verifier, not a translator.** The dealer-supplied EN is the source of truth. The LLM is the second pair of eyes that flags 16% of rows for human review. Don't overwrite `products.name_en` from LLM output without a human-approval step.

That's the system.

---

**File index for code-reading order:**

```
1.  PLAN.md §1-3                              (the brief + my interpretation)
2.  docs/decisions/ADR-002, ADR-005, ADR-007  (JSONB, section detection, LLM cost)
3.  src/storage/db/schema.ts                  (the data model)
4.  src/ingest/ (in file order)               (the parser)
5.  src/queue/                                (the orchestration)
6.  src/ai/ + src/cli/enrich.ts               (the LLM integration)
7.  src/api/                                  (the HTTP surface + streaming)
8.  test/unit/                                (32 tests, what's actually verified)
```
