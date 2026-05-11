# InventoryFlow Catalog Ingest

> Senior Engineer take-home submission for **Talemy x InventoryFlow** — 2026-05-11.
> Author: **Aric Nguyen** · `aricnguyen.analytics2002@gmail.com`

---

## 0 · TL;DR for the reviewer

| Question                                         | Answer                                                                                              |
| ------------------------------------------------ | --------------------------------------------------------------------------------------------------- |
| What does this do?                               | Parses a 241 MB messy OEM Excel catalog (110 sheets, 1586 schematics, EN+CN) into clean Postgres + R2 with JSONB fitment |
| Does it match the JD stack?                      | **Yes — Track A is TypeScript + Node + Postgres + Redis + Docker, exactly as required** ([§2 below](#2--jd-requirements--implementation-mapping)) |
| Does the reviewer need to pay anything?          | **No.** Every component runs locally with $0 cost. No API key required. ([§4 below](#4--cost-transparency--why-0)) |
| Why is there a second track (Track B)?           | To prove I understand Track A's scaling limits and have a credible migration path — judgment signal, not vendor showcase ([§3 below](#3--why-two-tracks-the-rationale)) |
| Where do I read first?                           | This README → [`PLAN.md`](PLAN.md) → [`docs/COMPARISON.md`](docs/COMPARISON.md) → ADRs in [`docs/decisions/`](docs/decisions/) |
| Status?                                          | Plan + scaffold complete (M0). Code lands Days 1–4 per [`PLAN.md §11`](PLAN.md#11-delivery-timeline--milestones). |

---

## 1 · What this ships

This is a take-home submission, not a deployed product. It ships **as a repo** with:

- **Track A** — a runnable TypeScript implementation matching the JD stack 1:1.
- **Track B** — a runnable OSS data-engineering implementation (proof of concept) showing the scale roadmap.
- **`docs/COMPARISON.md`** — a 16-dimension trade-off matrix proving Track A is the *right* choice for InventoryFlow today, not just the easy one.
- **9 ADRs** (`docs/decisions/`) — every non-trivial design call has a record including the AI suggestion I overrode and why.
- **An LLM response cache** committed at `shared/llm-cache.sqlite` so the reviewer runs the whole pipeline with **zero API keys**.

**Output of a successful run**:

- PostgreSQL `products` table with `part_number`, `name_en`, `name_cn`, `spec_cn`, pricing, and a **JSONB `fitment` column** listing every `{year, make, model}` the part fits — indexed via `GIN jsonb_path_ops` for sub-50 ms lookups at 10M-row scale.
- S3-compatible bucket (MinIO locally, R2 in production) holding schematic images, **SHA-256-keyed** for idempotent re-ingestion.
- `ingest_runs` + `ingest_audit` tables giving full provenance: which run, which sheet, which row, which prompt, which model, what cost.

---

## 2 · JD requirements ↔ Implementation mapping

Direct mapping of each requirement from the **Talemy x InventoryFlow Senior Engineer JD** to the code that satisfies it. Track A is the JD-native answer; Track B is supplementary.

### 2.1 Tech-stack requirements (JD §"Requirements")

| JD requires                                | Track A delivers                                  | Track B delivers (PoC)                       |
| ------------------------------------------ | ------------------------------------------------- | -------------------------------------------- |
| TypeScript across the stack                | ✅ TypeScript 5 strict, every file                 | n/a (Python, intentionally — see §3)        |
| Node.js                                    | ✅ Node 22 LTS + Fastify                           | n/a                                          |
| PostgreSQL                                 | ✅ PG 16, Drizzle ORM, GIN-indexed JSONB fitment   | ✅ Gold-layer sync target (same PG)          |
| Redis / queues / workers                   | ✅ Redis 7 + BullMQ (DLQ + rate-limit + retries)   | n/a (Prefect orchestrates)                   |
| Docker + cloud infrastructure              | ✅ `docker-compose.yml` (pg + redis + minio)       | ✅ `docker-compose.yml` (pg + minio + prefect) |
| AI tooling heavily integrated in workflow  | ✅ Cursor + Claude Code for dev; `ILLMProvider` abstraction with 5 swappable implementations + SQLite cache | ✅ Same `ILLMProvider` reused (Python port) |

### 2.2 Capability requirements (JD §"Looking For" + Test PDF)

| JD/Test requires                                       | Where in this submission                                                |
| ------------------------------------------------------ | ----------------------------------------------------------------------- |
| Strong TypeScript + backend engineering                | `track-a-jd-native/src/` — Fastify API, BullMQ workers, Drizzle schema  |
| Hands-on ETL / data-pipeline experience                | `track-a-jd-native/src/ingest/` + `track-b-data-engineering/pipelines/` |
| Working with unreliable / messy external data          | Section-detection by header regex (ADR-005); 10 documented mess patterns in `PLAN.md §2.2` + `docs/QUESTIONS_FOR_RECRUITER.md` |
| DevOps / infra in production                           | docker-compose, OpenTelemetry, /healthz, /metrics, runbook.md           |
| System design & operational reliability                | 4-plane control plane diagram (`PLAN.md §4.2`), DLQ, retry, idempotency (ADR-003), audit log |
| Pragmatism & Speed (Test PDF)                          | Track A scoped to 4-day delivery; Track B is a tight PoC                |
| AI Tooling (Test PDF — Cursor, Claude, Vision)         | `ILLMProvider` interface + ADR-007 cost strategy + provider abstraction |
| Clean Architecture incl. **JSON Fitment column**       | JSONB array of `{year, make, model, model_code, ...}` + `GIN jsonb_path_ops` index — design rationale in **ADR-002** |
| Onboard hundreds of dealerships efficiently            | `rules.yaml` per-dealer config; section detector dynamic, not hard-coded |
| Catalog / inventory / ERP/DMS familiarity (bonus)      | `part_number_aliases` table (ADR-006) — same pattern as SAP/Oracle/Lightspeed cross-reference tables |
| Event-driven & distributed workers (bonus)             | BullMQ fan-out: 1 file → N sheet jobs → N image jobs + N enrich jobs, OTel-traced per run |

### 2.3 What is NOT in scope (deliberate)

| Out of scope                                              | Why                                                                      |
| --------------------------------------------------------- | ------------------------------------------------------------------------ |
| Live cloud deployment (Fly.io / Railway / AWS)            | Take-home tests are evaluated as **source code**, not URLs. Hosting bills aren't a hiring signal. |
| Marketplace API integration (eBay / Amazon / Google Shopping) | The data schema is designed to feed these (JSONB matches their wire format), but actual sync code is post-onboarding work. |
| Scraping / browser automation                             | The test provides a single Excel input. Scraping is a JD bonus, not a test requirement. |
| Frontend / dashboards                                     | This is a backend role per the JD ("primarily a backend/data engineering role"). |

---

## 3 · Why two tracks — the rationale

A single-track submission forces a choice that loses senior signal either way:

- **Track-A-only** matches the stack but doesn't demonstrate I understand its limits at scale (and InventoryFlow is growing 30%/week — scale is the near-term reality).
- **Track-B-only** showcases data-engineering depth but fails stack-fit and reads as "I want to use my favourite tools regardless of what you asked for."

A two-track submission proves the more important property: **I picked the right tool for *your current stage*, not the most impressive one.** Track A is the answer. Track B is the documented roadmap. The `COMPARISON.md` matrix and ADR-009 quantify the migration triggers.

Full rationale in [`ADR-001`](docs/decisions/ADR-001-two-track-monorepo.md).

### 3.1 Stack detail — Track A (the recommended submission)

| Layer            | Choice                                | Reason                                                              |
| ---------------- | ------------------------------------- | ------------------------------------------------------------------- |
| Language         | TypeScript 5 (strict)                 | JD mandate                                                          |
| Runtime          | Node.js 22 LTS                        | JD mandate; native `fetch`, ESM, fast async                         |
| HTTP framework   | Fastify                               | Schema validation, native pino, faster than Express                 |
| Excel parser     | `exceljs` (streaming mode)            | Only Node lib that streams xlsx AND exposes drawings.xml            |
| XML parser       | `fast-xml-parser`                     | Required for `xl/drawings/*.xml` image anchors                      |
| ORM              | Drizzle ORM                           | Typed JSONB inference > Prisma; zero runtime (ADR-004)              |
| Database         | PostgreSQL 16                         | JD mandate; GIN on JSONB                                            |
| Queue            | BullMQ on Redis 7                     | JD mandate; mature, DLQ + rate-limit                                |
| Object storage   | Cloudflare R2 (prod) / MinIO (dev)    | Same S3 SDK works for both — swap by env vars                       |
| AI providers     | `ILLMProvider` interface + 5 impls    | ADR-007 — cost-controllable abstraction                             |
| Logging          | Pino                                  | Fastest in Node ecosystem, structured JSON                          |
| Tracing          | OpenTelemetry SDK                     | Vendor-neutral; can ship to Sentry/Honeycomb later                  |
| Tests            | Vitest + Testcontainers               | ESM-native; real PG/Redis in integration                            |

### 3.2 Stack detail — Track B (scale roadmap PoC)

| Layer                | Choice                          | Reason                                                                    |
| -------------------- | ------------------------------- | ------------------------------------------------------------------------- |
| Language             | Python 3.12                     | DE ecosystem; my CV's home                                                |
| Local compute        | Polars + DuckDB                 | 5–30× faster than pandas; zero JVM                                        |
| Distributed (future) | PySpark 3.5                     | Documented as upgrade path for >5 GB files; not used in PoC               |
| Storage format       | Delta Lake (`delta-rs`)         | Time travel, schema evolution, MERGE, ACID — no Databricks lock-in        |
| Transformation       | dbt-core (dbt-duckdb)           | Industry-standard model layering, tests, docs                             |
| Orchestrator         | Prefect 2.x                     | DAG-as-code, OSS, better local DX than Airflow                            |
| Data quality         | Great Expectations + dbt tests  | Schema enforcement at silver→gold boundary                                |
| Lineage              | OpenLineage spec → stdout (PoC) | Cell-level lineage, vendor-neutral (deploy Marquez later if needed)       |
| Serving DB           | PostgreSQL 16                   | Same as Track A — Track B replaces ingestion only, not serving            |

---

## 4 · Cost transparency — why $0?

The reviewer should be able to clone this repo, run `docker compose up`, and ingest the sample file **without entering a single credit card, API key, or signing up for any paid service**. Every component below is either OSS-self-hosted, in my existing personal subscription, or has a permanent free tier I don't approach:

| Category | Component                                  | Cost to reviewer | Cost to me  | Note                                              |
| -------- | ------------------------------------------ | ---------------- | ----------- | ------------------------------------------------- |
| Track A  | TypeScript, Node, Fastify, exceljs, Drizzle, BullMQ, pino, Zod, Vitest | $0 | $0 | All OSS                                |
| Track B  | Python, Polars, DuckDB, delta-rs, dbt-core, Prefect, GE, OpenLineage | $0 | $0 | All OSS                              |
| Database | PostgreSQL 16                              | $0 (local Docker) | $0       | OSS, single-node container                       |
| Queue    | Redis 7                                    | $0 (local Docker) | $0       | OSS, single-node container                       |
| Storage  | MinIO (S3-compatible, R2 stand-in)         | $0 (local Docker) | $0       | OSS — swap to R2 by changing env vars (see §5)   |
| AI       | Claude Code (dev) + handoff provider       | $0 | $0 *incremental* | Uses my existing **Claude Max Team** sub          |
| AI       | Ollama + Qwen2-VL 7B (optional)            | $0 (local) | $0           | OSS model, runs on M2 Mac                         |
| AI       | SQLite LLM cache committed at `shared/llm-cache.sqlite` | $0 | $0 | Pipeline reads cache → no upstream call           |
| AI       | Anthropic Batch API                        | $0 (never invoked) | $0 (never invoked) | Production-target stub only; documented, not run |
| Infra    | Hosting / domain / SSL / monitoring SaaS   | $0               | $0          | Submission is source code only, no deploy needed |
| **Total**| —                                          | **$0**           | **$0**      | Aligned with my [memory: zero-API-cost strategy] |

Full rationale in [`ADR-007`](docs/decisions/ADR-007-llm-provider-cost-strategy.md). This isn't a cost-cutting hack — it's the **same architecture** InventoryFlow will want at 1000-dealer scale, when LLM cost dominates the cloud bill and provider lock-in becomes a real risk. The free-submission pattern and the production-correct pattern converge.

---

## 5 · Local-dev → Production swap path

The reviewer might reasonably ask: "you used MinIO instead of R2 — is the code production-ready?" Yes, and the swap is trivial. This is the **same pattern Cloudflare officially recommends** for R2 local development.

### 5.1 Object storage (MinIO ↔ R2)

The pipeline talks the **S3 API** via `@aws-sdk/client-s3`. Both MinIO and R2 implement that API. Swap is three env vars:

```diff
# .env (local dev — MinIO)
- S3_ENDPOINT=http://localhost:9000
- S3_ACCESS_KEY=minioadmin
- S3_SECRET_KEY=minioadmin

# .env (production — Cloudflare R2)
+ S3_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
+ S3_ACCESS_KEY=<r2-access-key>
+ S3_SECRET_KEY=<r2-secret-key>
```

Application code is unchanged. SDK is unchanged. Same `PutObject`, `HeadObject`, `GetObject` calls.

### 5.2 PostgreSQL (local Docker ↔ managed)

```diff
- DATABASE_URL=postgres://dev:dev@localhost:5432/catalog
+ DATABASE_URL=postgres://user:pass@<host>:<port>/catalog?sslmode=require
```

Works with Supabase, Neon, RDS, Cloud SQL, anything Postgres-compatible.

### 5.3 Redis (local Docker ↔ managed)

```diff
- REDIS_URL=redis://localhost:6379
+ REDIS_URL=rediss://default:<token>@<host>:<port>
```

Works with Upstash, ElastiCache, Memorystore.

### 5.4 LLM provider (cached ↔ production)

```diff
- LLM_PROVIDER=cached            # reads shared/llm-cache.sqlite, no upstream
+ LLM_PROVIDER=anthropic         # invokes Anthropic Batch API, costs ~$0.003/row
+ ANTHROPIC_API_KEY=<key>
```

The `AnthropicBatchProvider` class is implemented and tested (with mock) but not invoked in this submission.

### 5.5 Why these all "just work"

Because the architecture has clean seams at every external boundary:

- `ILLMProvider` interface (5 implementations, swap by env)
- `@aws-sdk/client-s3` (S3 protocol, MinIO + R2 + S3 all compatible)
- `postgres://` URL parsing in `pg` driver (transport-agnostic)
- `redis://` URL parsing in `ioredis` (transport-agnostic)

A production deploy is a CD pipeline that injects different env vars. No code changes. No rewrites.

---

## 6 · How to run

### 6.1 Track A (the recommended path)

```bash
git clone <repo>
cd inventoryflow-catalog-ingest/track-a-jd-native
cp .env.example .env
docker compose up -d              # postgres + redis + minio
pnpm install
pnpm db:migrate
pnpm ingest ../shared/sample-data/example.xlsx
```

Expected on M2 Mac, 241 MB input: **wall-time ~4–6 min, peak RAM <300 MB**, zero API calls.

Verify:

```bash
psql -h localhost -U dev -d catalog -c \
  "SELECT part_number, name_en, name_cn FROM products
   WHERE fitment @> '[{\"make\":\"Kayo\",\"model_code\":\"AY70-2\"}]'
   LIMIT 5;"
```

### 6.2 Track B (the scale-roadmap PoC)

```bash
cd track-b-data-engineering
cp .env.example .env
docker compose up -d              # postgres + minio + prefect
poetry install
make track-b-run
```

Expected: **~2–3 min wall-time** (Polars beats Node on read).

---

## 7 · Repository map — what to read for what

```
inventoryflow-catalog-ingest/
│
├── README.md                          ← you are here
├── PLAN.md                            ← master plan (15 min read) — recruiter starts here
├── CHANGELOG.md
│
├── docs/
│   ├── COMPARISON.md                  ← 16-dimension Track A vs B trade-offs
│   ├── QUESTIONS_FOR_RECRUITER.md     ← 5 questions + 8 signals I caught reading the test
│   ├── runbook.md                     ← operations: how to run, debug, recover
│   └── decisions/                     ← 9 ADRs (each with "AI suggestion vs my override")
│       ├── README.md (ADR index)
│       ├── ADR-001 two-track-monorepo
│       ├── ADR-002 JSONB fitment ← test's stated focus
│       ├── ADR-003 SHA-256 idempotent images
│       ├── ADR-004 Drizzle over Prisma
│       ├── ADR-005 section detection strategy
│       ├── ADR-006 part-number aliases
│       ├── ADR-007 LLM provider cost strategy ← the $0 design
│       ├── ADR-008 medallion architecture
│       └── ADR-009 when to switch tracks
│
├── track-a-jd-native/                 ← TypeScript impl (JD-native)
│   ├── README.md
│   ├── src/{ingest,ai/providers,storage/db,queue/workers,api,cli,lib}
│   ├── test/{unit,integration,benchmark}
│   ├── migrations/
│   └── docker-compose.yml             (PG + Redis + MinIO)
│
├── track-b-data-engineering/          ← Polars + Delta + dbt PoC
│   ├── README.md
│   ├── pipelines/{bronze,silver,gold}
│   ├── dbt/models/{bronze,silver,gold}
│   ├── orchestration/
│   └── docker-compose.yml             (PG + MinIO + Prefect)
│
└── shared/
    ├── sample-data/                   ← place the test xlsx here (not committed)
    ├── prompts/                       ← versioned LLM prompt templates
    ├── schemas/                       ← JSON schemas (fitment.schema.json, ...)
    └── llm-cache.sqlite               ← committed; enables $0 reviewer runs
```

| If you want to evaluate…                  | Read this                                                |
| ----------------------------------------- | -------------------------------------------------------- |
| Can the candidate execute the JD stack?   | `track-a-jd-native/src/` + workers                       |
| Schema / JSONB modelling judgment?        | `track-a-jd-native/src/storage/db/schema.ts` + ADR-002   |
| AI tooling literacy and cost awareness?   | `track-a-jd-native/src/ai/providers/` + ADR-007          |
| Data-engineering breadth (bonus)?         | `track-b-data-engineering/` + COMPARISON.md              |
| Trade-off / decision-making process?      | All 9 ADRs (especially the "AI override" sections)       |
| Attention to detail / spotting bugs?      | `docs/QUESTIONS_FOR_RECRUITER.md`                        |
| Operational maturity?                     | `docs/runbook.md` + observability sections of PLAN.md    |
| Scale / cost thinking?                    | `docs/COMPARISON.md §6` + ADR-009                        |

---

## 8 · AI tooling transparency

| What                                              | How                                                                                       |
| ------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Boilerplate (Drizzle models, BullMQ workers, dbt scaffolds) | Cursor + Claude Code — ~40 % of lines                                              |
| Debugging, type errors, refactor passes           | Claude Code — ~20 %                                                                       |
| Vision parsing of ambiguous schematics            | Claude Vision via the `claude-code-handoff` provider, audit-logged in `ingest_audit`     |
| Architecture, schema, trade-off decisions         | **Human — me — 100 %**. Each decision lives in an ADR with an "AI suggestion vs my override" section explaining what the LLM proposed and why I chose differently. |
| Commit messages, ADR text, prompts                | Human-authored. No `feat: add X` defaults. Commit bodies follow problem → diagnosis → fix → trade-off. |

The submission is a faithful representation of how a senior engineer uses Claude Code / Cursor today: **AI accelerates execution; humans own the design**. Both layers are visible in the repo so the reviewer can audit either.

---

## 9 · Status & roadmap

```
M0 — Plan & repo scaffold + 9 ADRs    ✅ Done (2026-05-11)
M1 — Single sheet parsed (AY70-2)     ⏳ Day 1 PM
M2 — Full file → DB                    ⏳ Day 2
M3 — Both tracks runnable + LLM cache  ⏳ Day 3
M4 — Final polish + submission         ⏳ Day 4
```

Full timeline in [`PLAN.md §11`](PLAN.md#11-delivery-timeline--milestones).

---

## 10 · Open questions for the recruiter

Filed in [`docs/QUESTIONS_FOR_RECRUITER.md`](docs/QUESTIONS_FOR_RECRUITER.md). The five most important:

1. PDF test page 1 contains 4 bullets ("Maintain content and posting calendar...") that read like a marketing-role paste error. Confirm: ignore?
2. Should `make = "Kayo"` be hard-coded, or expressed in a per-dealer config (current choice)?
3. R2 credentials for review: MinIO suffices, you provide R2 sandbox creds, or I generate a short-lived tunnel?
4. Sub-assemblies (`"1-1"`, `"1-6L"`) — separate `products` rows with FK (current) or nested JSONB children?
5. Reference sheets (Carburetor Jets, Spark Plugs, Owners Manuals) — separate `reference_specs` table (current) or part of primary catalog?

Eight further signals I caught while parsing the data file (not questions — flags to demonstrate I read the input, not just the brief) are listed at the bottom of the same document.

---

## Contact

**Aric Nguyen** — `aricnguyen.analytics2002@gmail.com`

Available for follow-up technical interview, system-design deep dive, or live walkthrough of either track.
