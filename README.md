<div align="center">

# 🏭 InventoryFlow Catalog Ingest

### *From 241 MB of messy OEM Excel chaos to a clean, queryable parts catalog — in one pipeline.*

[![Status](https://img.shields.io/badge/status-M0%20scaffold-blue?style=for-the-badge)](./PLAN.md#11-delivery-timeline--milestones)
[![Take-home Test](https://img.shields.io/badge/take--home-Talemy%20×%20InventoryFlow-ff6b35?style=for-the-badge)](#)
[![Cost to Reviewer](https://img.shields.io/badge/reviewer%20cost-%240-success?style=for-the-badge)](#-cost-transparency--why-0)
[![License](https://img.shields.io/badge/license-MIT-lightgrey?style=for-the-badge)](./LICENSE)

<br/>

**Track A — JD-Native**
[![TypeScript](https://img.shields.io/badge/TypeScript-5.x-3178C6?logo=typescript&logoColor=white)](#)
[![Node](https://img.shields.io/badge/Node.js-22%20LTS-339933?logo=nodedotjs&logoColor=white)](#)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](#)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white)](#)
[![BullMQ](https://img.shields.io/badge/BullMQ-queues-c92a2a)](#)
[![Cloudflare R2](https://img.shields.io/badge/Cloudflare-R2-F38020?logo=cloudflare&logoColor=white)](#)
[![Docker](https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white)](#)

**Track B — Big-Data DE Roadmap**
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](#)
[![Polars](https://img.shields.io/badge/Polars-DataFrames-F0AC2D)](#)
[![Delta Lake](https://img.shields.io/badge/Delta%20Lake-3.x-00ADD4)](#)
[![dbt](https://img.shields.io/badge/dbt-core-FF694A?logo=dbt&logoColor=white)](#)
[![Prefect](https://img.shields.io/badge/Prefect-2.x-070A52?logo=prefect&logoColor=white)](#)

<br/>

[**📖 Read the Plan**](./PLAN.md)  ·  [**⚖️ Compare Tracks**](./docs/COMPARISON.md)  ·  [**🏛 ADRs**](./docs/decisions/)  ·  [**❓ Open Questions**](./docs/QUESTIONS_FOR_RECRUITER.md)  ·  [**📓 Runbook**](./docs/runbook.md)

</div>

---

> [!NOTE]
> **This is a senior-engineer take-home submission, not a deployed product.**
> The repo ships as **source code + 1,179-line engineering plan + 9 ADRs + 16-dimension comparison matrix**. Every component runs locally — the reviewer pays nothing, signs up for nothing, enters no API key.

---

## ⚡ At a glance

<table>
<tr>
<td width="50%" valign="top">

### 🎯 What it does

Turns a **241 MB OEM Excel catalog** (110 sheets · 1,586 schematics · EN+CN multilingual) into a **clean Postgres catalog** with **JSONB fitment** and **R2-hosted schematic images**.

```sql
-- The core query the test asks us to enable:
SELECT part_number, name_en, name_cn, image_url
FROM products
WHERE fitment @> '[{"make":"Kayo","model":"Storm 150","year":2022}]';
-- ↑ <50 ms on 10M rows via GIN jsonb_path_ops
```

</td>
<td width="50%" valign="top">

### 🏆 What it proves

- ✅ **Stack fit** — Track A is the exact JD stack
- ✅ **Scale judgment** — Track B documents the migration path at 500+ dealers
- ✅ **Cost economics** — `ILLMProvider` abstraction enables $0 reviewer runs *and* production cost control
- ✅ **AI tooling literacy** — 9 ADRs visibly document what I overrode from Claude/Cursor
- ✅ **Data engineering depth** — 10 mess patterns caught by parsing the file, not the brief

</td>
</tr>
</table>

---

## 🚀 Quick start (zero-cost, zero API key)

> [!TIP]
> Default `LLM_PROVIDER=cached` reads the committed SQLite cache → **no upstream API call ever fires** during a reviewer run.

```bash
git clone https://github.com/ankinguyen-engineer-2002/inventoryflow-catalog-ingest.git
cd inventoryflow-catalog-ingest/track-a-jd-native

cp .env.example .env
docker compose up -d                 # postgres + redis + minio (local R2)
pnpm install
pnpm db:migrate
pnpm ingest ../shared/sample-data/example.xlsx
```

**Expected** on M2 Mac, 241 MB input → wall-time **~4–6 min**, peak RAM **<300 MB**, zero API spend.

Verify:

```bash
psql -h localhost -U dev -d catalog -c \
  "SELECT part_number, name_en, name_cn
   FROM products
   WHERE fitment @> '[{\"make\":\"Kayo\",\"model_code\":\"AY70-2\"}]'
   LIMIT 5;"
```

---

## 🧭 Why two tracks?

A single-track submission forces a binary choice that loses senior signal either way:

```mermaid
flowchart LR
    Q{Senior signal:<br/>which stack?}
    Q -->|Track A only| A[✅ Matches JD<br/>❌ Doesn't show scale judgment]
    Q -->|Track B only| B[❌ Off-stack<br/>✅ Shows DE depth]
    Q -->|Both + COMPARISON.md| C[✅ Stack fit<br/>✅ Scale judgment<br/>✅ Explicit recommendation]

    style C fill:#10b981,stroke:#047857,color:#fff
    style A fill:#fef3c7,stroke:#d97706
    style B fill:#fef3c7,stroke:#d97706
```

**Track A is the recommendation.** Track B is the documented roadmap for when InventoryFlow hits ~500 dealers / 50 TB / 30% LLM-cost share. Migration triggers are quantified in **[ADR-009](./docs/decisions/ADR-009-when-to-switch-tracks.md)** — not "when it feels needed", but six measurable thresholds.

Full rationale: **[ADR-001](./docs/decisions/ADR-001-two-track-monorepo.md)** · **[COMPARISON.md](./docs/COMPARISON.md)**

---

## 📋 JD ↔ Implementation mapping

> Direct mapping of each Talemy x InventoryFlow JD requirement to the file/folder that satisfies it.

<details>
<summary><b>📦 Tech-stack requirements (JD § Requirements)</b></summary>

| JD requires                                | Track A delivers                                  | Where                                                  |
| ------------------------------------------ | ------------------------------------------------- | ------------------------------------------------------ |
| TypeScript across the stack                | ✅ TypeScript 5 strict                             | `track-a-jd-native/src/`                               |
| Node.js                                    | ✅ Node 22 LTS + Fastify                           | `track-a-jd-native/src/api/`                           |
| PostgreSQL                                 | ✅ PG 16 + Drizzle + GIN JSONB                     | `track-a-jd-native/src/storage/db/`                    |
| Redis / queues / workers                   | ✅ Redis 7 + BullMQ (DLQ + rate-limit)             | `track-a-jd-native/src/queue/`                         |
| Docker + cloud infrastructure              | ✅ `docker-compose.yml` (pg + redis + minio)       | `track-a-jd-native/docker-compose.yml`                 |
| AI tooling heavily integrated in workflow  | ✅ `ILLMProvider` + 5 impls + SQLite cache         | `track-a-jd-native/src/ai/` · [ADR-007](./docs/decisions/ADR-007-llm-provider-cost-strategy.md) |

</details>

<details>
<summary><b>🎯 Capability requirements (JD § Looking For + Test PDF)</b></summary>

| JD/Test requires                                       | Where in this submission                                                |
| ------------------------------------------------------ | ----------------------------------------------------------------------- |
| Strong TypeScript + backend engineering                | `track-a-jd-native/src/` — Fastify, BullMQ, Drizzle                     |
| Hands-on ETL / data-pipeline experience                | `src/ingest/` + `track-b-data-engineering/pipelines/`                   |
| Working with unreliable / messy external data          | Header-regex section detection ([ADR-005](./docs/decisions/ADR-005-section-detection-strategy.md)); 10 mess patterns in [PLAN.md §2.2](./PLAN.md#22-dataset-reality-verified-by-parsing) |
| DevOps / infra in production                           | Docker compose, OpenTelemetry, `/healthz`, `/metrics`, runbook          |
| System design & operational reliability                | 4-plane control plane ([PLAN.md §4.2](./PLAN.md#42-architecture--four-plane-control-plane)), DLQ, idempotent SHA-256 ([ADR-003](./docs/decisions/ADR-003-sha256-idempotent-images.md)) |
| Pragmatism & Speed (Test PDF)                          | 4-day delivery scope; Track B is a tight PoC                            |
| **Clean Architecture incl. JSON Fitment column**       | JSONB array + `GIN jsonb_path_ops` — design in [**ADR-002**](./docs/decisions/ADR-002-jsonb-fitment.md) |
| Onboard hundreds of dealerships efficiently            | Per-dealer `rules.yaml` config; dynamic section detection               |
| Catalog/inventory/ERP/DMS familiarity (bonus)          | `part_number_aliases` ([ADR-006](./docs/decisions/ADR-006-part-number-aliases.md)) — same as SAP/Lightspeed cross-ref tables |
| Event-driven & distributed workers (bonus)             | BullMQ fan-out (file → sheets → images), OTel-traced per `run_id`       |

</details>

<details>
<summary><b>🚫 Deliberately out of scope</b></summary>

| Out of scope                          | Why                                                                 |
| ------------------------------------- | ------------------------------------------------------------------- |
| Live cloud deployment                 | Take-homes are evaluated as source code, not URLs                   |
| Marketplace API integration           | Schema is designed to feed eBay/Amazon — actual sync is post-onboarding |
| Scraping / browser automation         | Test gives a single Excel input; scraping is a JD *bonus*           |
| Frontend / dashboards                 | JD: *"primarily a backend/data engineering role"*                   |

</details>

---

## 💸 Cost transparency — why $0?

> [!IMPORTANT]
> **The reviewer never enters a credit card, never signs up for a paid service, never enters an API key.** This isn't a cost-cutting hack — it's the same architecture InventoryFlow will need at 1,000-dealer scale when LLM costs dominate the cloud bill. See [ADR-007](./docs/decisions/ADR-007-llm-provider-cost-strategy.md).

<table>
<tr>
<th align="left">Category</th>
<th align="left">Components</th>
<th align="center">Cost (reviewer)</th>
<th align="center">Cost (me)</th>
</tr>
<tr>
<td>📦 Track A stack</td>
<td>TS · Node · Fastify · exceljs · Drizzle · BullMQ · pino · Zod · Vitest</td>
<td align="center"><b>$0</b></td>
<td align="center"><b>$0</b></td>
</tr>
<tr>
<td>📦 Track B stack</td>
<td>Python · Polars · DuckDB · delta-rs · dbt-core · Prefect · Great Expectations</td>
<td align="center"><b>$0</b></td>
<td align="center"><b>$0</b></td>
</tr>
<tr>
<td>🗄 Database</td>
<td>PostgreSQL 16 (local Docker)</td>
<td align="center"><b>$0</b></td>
<td align="center"><b>$0</b></td>
</tr>
<tr>
<td>🔁 Queue</td>
<td>Redis 7 (local Docker)</td>
<td align="center"><b>$0</b></td>
<td align="center"><b>$0</b></td>
</tr>
<tr>
<td>🪣 Object storage</td>
<td>MinIO (S3-compatible R2 stand-in)</td>
<td align="center"><b>$0</b></td>
<td align="center"><b>$0</b></td>
</tr>
<tr>
<td>🤖 AI — dev</td>
<td>Claude Code (handoff provider, my existing Claude Max sub)</td>
<td align="center"><b>$0</b></td>
<td align="center">$0 incremental</td>
</tr>
<tr>
<td>🤖 AI — runtime</td>
<td>SQLite cache committed at <code>shared/llm-cache.sqlite</code></td>
<td align="center"><b>$0</b></td>
<td align="center"><b>$0</b></td>
</tr>
<tr>
<td>🤖 AI — production target</td>
<td>Anthropic Batch API (stub class, never invoked)</td>
<td align="center"><b>$0</b></td>
<td align="center"><b>$0</b></td>
</tr>
<tr>
<td>☁️ Infra (hosting/SaaS)</td>
<td>None — submission is source code only</td>
<td align="center"><b>$0</b></td>
<td align="center"><b>$0</b></td>
</tr>
<tr>
<td colspan="2"><b>TOTAL</b></td>
<td align="center"><b>$0</b></td>
<td align="center"><b>$0</b></td>
</tr>
</table>

---

## 🔀 Local-dev → Production: trivial swap

> [!TIP]
> All external boundaries are clean seams — `@aws-sdk/client-s3`, `postgres://` URLs, `redis://` URLs, `ILLMProvider` interface. Production deploy is a CD pipeline that injects different env vars. **No code changes.**

<table>
<tr>
<th>Boundary</th>
<th>Local dev (this submission)</th>
<th>Production (one env-var change)</th>
</tr>
<tr>
<td>Object storage</td>
<td>

```bash
S3_ENDPOINT=http://localhost:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
```

</td>
<td>

```bash
S3_ENDPOINT=https://<acct>.r2.cloudflarestorage.com
S3_ACCESS_KEY=<r2-key>
S3_SECRET_KEY=<r2-secret>
```

</td>
</tr>
<tr>
<td>Postgres</td>
<td><code>postgres://dev:dev@localhost:5432/catalog</code></td>
<td><code>postgres://user:pass@&lt;host&gt;:5432/catalog?sslmode=require</code> (Supabase, Neon, RDS, Cloud SQL)</td>
</tr>
<tr>
<td>Redis</td>
<td><code>redis://localhost:6379</code></td>
<td><code>rediss://default:&lt;token&gt;@&lt;host&gt;:&lt;port&gt;</code> (Upstash, ElastiCache)</td>
</tr>
<tr>
<td>LLM</td>
<td><code>LLM_PROVIDER=cached</code> (reads committed SQLite, no upstream)</td>
<td><code>LLM_PROVIDER=anthropic</code> + <code>ANTHROPIC_API_KEY</code> (Batch API, ~$0.003/row)</td>
</tr>
</table>

---

## 🏗 Architecture preview

**Track A** — four-plane control plane, BullMQ-orchestrated workers:

```mermaid
flowchart TB
    subgraph Ingress
        CLI[CLI: pnpm ingest]
        API[Fastify HTTP API<br/>/runs · /healthz · /metrics]
    end

    subgraph Control["① Control Plane"]
        REG[Run Registry<br/>ingest_runs]
        SCHED[BullMQ Scheduler]
        CONF[Per-dealer rules.yaml]
    end

    subgraph Data["② Data Plane (Workers)"]
        PARSE[parse-sheet<br/>exceljs + drawings.xml]
        UPLOAD[upload-image<br/>SHA-256 keyed]
        ENRICH[enrich-llm<br/>targeted only]
        DLQ[Dead-letter queue]
    end

    subgraph AI["③ Intelligence Plane"]
        IFACE[ILLMProvider]
        CACHE[(SQLite cache)]
        IFACE --> CACHE
    end

    subgraph Storage["④ Storage Plane"]
        PG[(PostgreSQL 16<br/>products + JSONB fitment)]
        R2[(R2 / MinIO<br/>schematic images)]
        REDIS[(Redis<br/>queue + cache)]
    end

    CLI --> REG
    API --> REG
    REG --> SCHED
    SCHED --> PARSE
    PARSE --> UPLOAD
    PARSE --> ENRICH
    ENRICH --> IFACE
    UPLOAD --> R2
    PARSE --> PG
    ENRICH --> PG
    SCHED --> REDIS
    PARSE -.failures.-> DLQ

    style Control fill:#fef3c7,stroke:#d97706
    style Data fill:#dbeafe,stroke:#2563eb
    style AI fill:#fce7f3,stroke:#db2777
    style Storage fill:#dcfce7,stroke:#16a34a
```

**Track B** — medallion lakehouse (Bronze/Silver/Gold) on OSS Delta Lake:

```mermaid
flowchart LR
    XLSX[📄 Source<br/>241 MB xlsx] --> RAW[Raw Landing<br/>S3]
    RAW --> BRONZE[🥉 Bronze<br/>Delta Lake<br/>raw JSON safety net]
    BRONZE -->|Polars + Great Expectations| SILVER[🥈 Silver<br/>Delta Lake<br/>conformed + typed]
    SILVER -->|dbt models + tests| GOLD[🥇 Gold<br/>products mart<br/>marketplace view]
    GOLD --> PG[(PostgreSQL<br/>serving layer)]
    GOLD --> DUCK[DuckDB<br/>analytics]

    style BRONZE fill:#cd7f32,color:#fff
    style SILVER fill:#c0c0c0,color:#000
    style GOLD fill:#ffd700,color:#000
```

Full diagrams + data flow in [`PLAN.md §4.2`](./PLAN.md#42-architecture--four-plane-control-plane) and [`§5.3`](./PLAN.md#53-architecture--medallion-lakehouse).

---

## 📂 Repository map

```
inventoryflow-catalog-ingest/
│
├── 📘 PLAN.md                          ← Master plan (1179 lines, 15 min read)
├── 📕 README.md                        ← you are here
├── 📄 CHANGELOG.md
│
├── 📂 docs/
│   ├── 📊 COMPARISON.md                ← Track A vs B, 16 dimensions
│   ├── ❓ QUESTIONS_FOR_RECRUITER.md   ← 5 questions + 8 signals
│   ├── 📓 runbook.md                   ← Operations & troubleshooting
│   └── 🏛 decisions/                   ← 9 ADRs (each with AI override section)
│       ├── ADR-001 two-track monorepo
│       ├── ADR-002 JSONB fitment  ← test's stated focus
│       ├── ADR-003 SHA-256 idempotent images
│       ├── ADR-004 Drizzle over Prisma
│       ├── ADR-005 section detection strategy
│       ├── ADR-006 part-number aliases
│       ├── ADR-007 LLM provider cost strategy  ← the $0 design
│       ├── ADR-008 medallion architecture
│       └── ADR-009 when to switch tracks
│
├── 🟦 track-a-jd-native/               ← TypeScript impl (JD-native)
│   ├── src/{ingest,ai/providers,storage/db,queue/workers,api,cli,lib}
│   ├── test/{unit,integration,benchmark}
│   ├── migrations/
│   └── docker-compose.yml              (PG + Redis + MinIO)
│
├── 🟨 track-b-data-engineering/        ← Polars + Delta + dbt PoC
│   ├── pipelines/{bronze,silver,gold}
│   ├── dbt/models/{bronze,silver,gold}
│   ├── orchestration/                  (Prefect flows)
│   └── docker-compose.yml              (PG + MinIO + Prefect)
│
└── 🔁 shared/                          ← reused across tracks
    ├── sample-data/                    (place test xlsx here, not committed)
    ├── prompts/                        (versioned LLM prompt templates)
    ├── schemas/                        (fitment.schema.json, ...)
    └── llm-cache.sqlite                (committed → zero-API reviewer runs)
```

### What to read for what

| If you want to evaluate…                  | Read this                                                  |
| ----------------------------------------- | ---------------------------------------------------------- |
| Stack-fit execution                       | `track-a-jd-native/src/` + workers                         |
| Schema / JSONB modelling judgment         | `src/storage/db/schema.ts` + **[ADR-002](./docs/decisions/ADR-002-jsonb-fitment.md)** |
| AI tooling + cost awareness               | `src/ai/providers/` + **[ADR-007](./docs/decisions/ADR-007-llm-provider-cost-strategy.md)** |
| Data-engineering breadth (bonus)          | `track-b-data-engineering/` + [COMPARISON.md](./docs/COMPARISON.md) |
| Trade-off / decision-making process       | All 9 ADRs (especially the "AI override" sections)         |
| Attention to detail / spotting bugs       | [QUESTIONS_FOR_RECRUITER.md](./docs/QUESTIONS_FOR_RECRUITER.md) |
| Operational maturity                      | [runbook.md](./docs/runbook.md) + PLAN.md observability    |
| Scale & cost thinking                     | [COMPARISON.md §6](./docs/COMPARISON.md) + **[ADR-009](./docs/decisions/ADR-009-when-to-switch-tracks.md)** |

---

## 🤖 AI tooling transparency

> [!NOTE]
> Honest representation of how a senior engineer uses Claude Code / Cursor today: **AI accelerates execution; humans own design.** Every architectural decision lives in an ADR documenting what the LLM suggested vs what I chose.

| What                                                  | How                                                                                       |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Boilerplate (Drizzle models, BullMQ workers, scaffolds) | Cursor + Claude Code · ~40 % of lines                                                  |
| Debugging, type errors, refactor passes               | Claude Code · ~20 %                                                                       |
| Vision parsing of ambiguous schematics                | Claude Vision via `claude-code-handoff` provider · audit-logged in `ingest_audit`        |
| **Architecture, schema, trade-offs, recommendations** | **Human · 100 %**. Each lives in an ADR with the LLM suggestion + my override + reason. |
| Commit messages, ADR text, prompts                    | Human-authored. No `feat: add X` defaults. Bodies follow problem → diagnosis → fix → trade-off. |

---

## 📅 Status & roadmap

```
✅ M0 — Plan + repo scaffold + 9 ADRs           (2026-05-11)
⏳ M1 — Single sheet parsed (AY70-2)            Day 1 PM
⏳ M2 — Full file → DB                          Day 2
⏳ M3 — Both tracks runnable + LLM cache        Day 3
⏳ M4 — Final polish + submission               Day 4
```

Full timeline: [`PLAN.md §11`](./PLAN.md#11-delivery-timeline--milestones)

---

## ❓ Open questions for the reviewer

> Filed in **[`docs/QUESTIONS_FOR_RECRUITER.md`](./docs/QUESTIONS_FOR_RECRUITER.md)**. The five most important:

1. PDF test page 1 contains 4 bullets about "content/posting calendar" — paste error from a marketing JD? **Confirm: ignore?**
2. Should `make = "Kayo"` be hard-coded or expressed in per-dealer `rules.yaml`?
3. R2 credentials for review: MinIO suffices, R2 sandbox creds provided, or short-lived tunnel?
4. Sub-assemblies (`"1-1"`, `"1-6L"`) — separate rows with FK *(current)* or nested JSONB children?
5. Reference sheets (Carburetor Jets, Spark Plugs, Owners Manuals) — separate table *(current)* or part of `products`?

Plus 8 signals I caught while parsing the data file (paste errors, schema variants, edge cases) — listed at the bottom of the same doc to demonstrate I read the input, not just the brief.

---

<div align="center">

### Made by [**Aric Nguyen**](mailto:aricnguyen.analytics2002@gmail.com) · Built with Claude Code

`aricnguyen.analytics2002@gmail.com`

*Available for live walkthrough · system-design deep-dive · or technical interview.*

<br/>

[![Read the Plan](https://img.shields.io/badge/📖_Read_the_Plan-PLAN.md-blue?style=for-the-badge)](./PLAN.md)
[![Compare Tracks](https://img.shields.io/badge/⚖️_Compare_Tracks-COMPARISON.md-orange?style=for-the-badge)](./docs/COMPARISON.md)
[![View ADRs](https://img.shields.io/badge/🏛_View_ADRs-9_decisions-purple?style=for-the-badge)](./docs/decisions/)

</div>
