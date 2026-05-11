# Solution C — Microsoft Fabric v10 Control Plane

> **Status:** Architecture-only. Not implemented in this submission.
> **Positioning:** Enterprise-grade lakehouse + serving + governance on a single platform with a metadata-driven control plane. Worth choosing when the organisation has already standardised on Microsoft 365, Azure AD, and Power BI, and when the requirement is fewer moving parts rather than maximum stack freedom.

---

## Premise

Solution A optimises for **JD stack fit**. Solution B optimises for **modern OSS portability**. Solution C optimises for **integrated enterprise platform with a metadata-driven control plane that scales from one dealer to ten thousand without re-architecture**.

Microsoft Fabric is the integrated SaaS platform that combines OneLake (the unified lakehouse), Lakehouse and Warehouse (compute over OneLake), Data Factory (pipeline orchestration), Real-Time Intelligence (Eventhouse / KQL Database for streaming and analytics), Power BI (semantic models with Direct Lake), Dataverse (relational metadata + low-code apps), and Activator (event-driven automation) under a single tenant, single security model, and single billing surface.

The "v10 control plane" pattern adopted here is one I have implemented in production at a previous client (Ashley): every dealer, every ingestion pattern, every schedule lives as a row in a Dataverse table. A single generic Data Factory pipeline iterates over those rows, dispatches to the right notebook or pipeline handler, and writes provenance and run state back to Dataverse. Onboarding a new dealer becomes a Power Apps form submission rather than a development cycle.

---

## Stack

| Layer                              | Microsoft Fabric component                       | Role                                                                 |
| ---------------------------------- | ------------------------------------------------ | -------------------------------------------------------------------- |
| Unified storage                    | **OneLake**                                       | Single logical lake; every Fabric workload reads and writes here     |
| Bronze / silver / gold tables      | **Lakehouse** (Delta Parquet on OneLake)          | Medallion architecture; ACID transactions; time-travel               |
| Curated analytical store           | **Warehouse** (SQL endpoint on OneLake)           | T-SQL analytics; serves Power BI Direct Lake                         |
| Compute — batch ingestion          | **Notebooks** (PySpark + Spark SQL)               | Reads xlsx, transforms, writes Delta tables                          |
| Compute — orchestration            | **Data Factory pipelines (metadata-driven)**       | Generic pipeline reads `dealer_bindings` from Dataverse, dispatches  |
| Streaming and analytics            | **Eventhouse / KQL Database**                     | Real-time ingestion, KQL ad-hoc analytics, materialised views        |
| Streaming source                   | **Eventstream**                                   | Connects Lightspeed / eBay webhooks to Eventhouse                    |
| Semantic layer                     | **Power BI semantic model (Direct Lake)**         | Sub-second BI queries directly on OneLake parquet without import     |
| Operational metadata               | **Dataverse**                                     | The `dealers`, `ingestion_patterns`, `dealer_bindings` registries    |
| Self-service dealer portal         | **Power Apps**                                    | Form-driven dealer onboarding, schedule changes, schema overrides    |
| Event-driven automation            | **Activator**                                     | Threshold-triggered actions (low stock → Teams notification, etc.)   |
| Identity                           | **Microsoft Entra ID (Azure AD)**                  | Single sign-on, RBAC, per-workspace permissions                       |
| Cost monitoring                    | **Fabric Capacity Metrics app**                    | Per-workspace, per-item Capacity Unit (CU) consumption                |
| ML and embeddings                  | **Azure OpenAI** (called from Notebooks)          | LLM translation; same `ILLMProvider` abstraction surface             |
| Governance and lineage             | **Microsoft Purview**                              | Asset catalog, lineage from source to Power BI report, sensitivity   |

Every component lives inside one Fabric tenant. There is no separate compute cluster to provision, no separate streaming infrastructure to operate, no separate identity store to integrate. The trade-off is total commitment to Microsoft as the platform.

---

## Architecture

```
              ┌────────────────────────────────────────────────────────────┐
              │                  Microsoft Fabric tenant                    │
              │  (single Azure AD, single billing, single Purview catalog)  │
              └──────────────────────────────┬─────────────────────────────┘
                                             │
              ┌──────────────────────────────▼─────────────────────────────┐
              │                       OneLake                              │
              │  Single unified storage; ADLS Gen2-compatible; every        │
              │  Fabric workload writes and reads here                      │
              └────┬───────────────┬────────────┬───────────────┬──────────┘
                   │               │            │               │
            ┌──────▼─────┐   ┌─────▼─────┐  ┌───▼──────┐   ┌────▼──────────┐
            │  BRONZE    │   │  SILVER   │  │  GOLD    │   │  Eventhouse   │
            │  Lakehouse │   │ Lakehouse │  │Warehouse │   │ (KQL Database)│
            │            │   │           │  │          │   │               │
            │ Raw xlsx   │   │ Conformed │  │ Marts:   │   │ Real-time     │
            │ → Delta    │   │ parts,    │  │ products,│   │ inventory,    │
            │ rows       │   │ fitment,  │  │ market-  │   │ pricing,      │
            │            │   │ images    │  │ place_   │   │ order events  │
            │            │   │           │  │ view     │   │               │
            └──────▲─────┘   └─────▲─────┘  └────▲─────┘   └──────▲────────┘
                   │               │             │                │
                   │               │             │                │
            ┌──────┴───────────────┴─────────────┴────────┐  ┌───┴──────────┐
            │  Data Factory metadata-driven pipeline      │  │  Eventstream │
            │  ────────────────────────────────────────   │  │              │
            │  ForEach (dealer_binding IN Dataverse):     │  │  Sources:    │
            │    ├ Lookup pattern handler                 │  │   Lightspeed │
            │    ├ Invoke matching Notebook               │  │   webhook    │
            │    │   (PySpark: parse, normalise, write    │  │   eBay event │
            │    │    Delta)                              │  │   bridge     │
            │    └ Update binding.last_run_at / sha256    │  │              │
            │                                              │  │  → Eventhouse│
            │  Triggers:                                   │  │              │
            │    ├ Schedule trigger (cron)                 │  └──────────────┘
            │    ├ Event trigger (file landing)             │
            │    └ Manual / Power Apps invocation           │
            └──────────────────────┬──────────────────────┘
                                   │
                  ┌────────────────▼────────────────┐
                  │            Dataverse             │
                  │  ──────────────                  │
                  │  dealers                          │
                  │  ingestion_patterns               │
                  │  dealer_bindings                  │
                  │  run_history                      │
                  │  audit_log                        │
                  │  schema_overrides                 │
                  │                                   │
                  │  Power Apps form on top:          │
                  │   Dealer onboarding wizard        │
                  │   Schedule reconfiguration         │
                  │   Schema override approvals        │
                  └─────────────────┬─────────────────┘
                                    │
                  ┌─────────────────▼────────────────┐
                  │  Activator                        │
                  │  ──────────                       │
                  │  Conditions:                      │
                  │   stock_level < threshold         │
                  │   ingest_run.status = FAILED      │
                  │   schema_signature_change         │
                  │                                   │
                  │  Actions:                         │
                  │   Teams notification              │
                  │   Email to dealer success         │
                  │   Trigger remediation pipeline    │
                  └───────────────────────────────────┘

  ┌──────────────────────────────────────────────────────────────────────┐
  │  Consumers                                                            │
  │                                                                       │
  │  Power BI Direct Lake (sub-second BI on OneLake parquet, no import)  │
  │  Power Apps catalog browser / dealer portal                           │
  │  Microsoft Fabric SQL endpoint (T-SQL ad-hoc)                          │
  │  External marketplace sync (REST / GraphQL via API Management)         │
  │  Microsoft Purview (lineage, catalog, sensitivity labels)              │
  └──────────────────────────────────────────────────────────────────────┘
```

---

## The v10 control plane in detail

The metadata-driven dispatch pattern that distinguishes a mature Fabric deployment from a Notebook-per-table deployment.

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Dataverse — control plane tables                                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│ dealers                          One row per OEM / distributor          │
│   ├ name                                                                 │
│   ├ inferred_make                                                        │
│   ├ status (ACTIVE / PAUSED / OFFBOARDED)                                │
│   ├ tier (FREE / STANDARD / ENTERPRISE)                                  │
│   └ tenant_security_group        (Entra ID group for RLS)                │
│                                                                          │
│ ingestion_patterns               Handler registry                        │
│   ├ pattern_name                 e.g. "xlsx_oem_catalog_v1"              │
│   ├ pattern_type                 FILE_BATCH / API_PUSH / CDC / STREAM    │
│   ├ handler_notebook_path        e.g. "notebooks/parsers/xlsx_v1.ipynb"  │
│   ├ schema_signature             JSONB: expected columns + types         │
│   ├ validation_rules             JSONB: Great Expectations equivalents   │
│   ├ default_freshness_sla        ISO 8601 duration: PT24H, PT5M          │
│   ├ default_schedule             cron | 'event-driven' | 'on-source-change'│
│   ├ version                                                              │
│   └ deprecated_at                                                        │
│                                                                          │
│ dealer_bindings                  Dealer × pattern assignments            │
│   ├ dealer_id                    FK → dealers                            │
│   ├ pattern_name                 FK → ingestion_patterns                 │
│   ├ params                       JSONB: source_glob, secrets ref, etc.    │
│   ├ freshness_sla_override                                               │
│   ├ schedule_override                                                    │
│   ├ enabled                                                              │
│   ├ last_run_at                                                          │
│   ├ last_run_sha256              For cron-smart skip                     │
│   └ last_run_id                                                          │
│                                                                          │
│ run_history                      Provenance and audit                    │
│ schema_overrides                 Per-dealer column rename / drop         │
│ audit_log                        Every LLM call, every binding mutation  │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  │ ForEach loop reads bindings
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ Data Factory pipeline — one generic pipeline for all dealers             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  pipeline: master_ingest                                                 │
│    activities:                                                           │
│      1. Lookup dealer_bindings WHERE enabled = true                      │
│      2. ForEach binding:                                                 │
│         a. Evaluate should_run (cron / sha256 / freshness SLA)           │
│         b. If skip: write SKIPPED row to run_history with reason         │
│         c. If dispatch: invoke handler_notebook with binding.params      │
│         d. On handler success: update last_run_at, last_run_sha256       │
│         e. On handler failure: write FAILED row + trigger Activator     │
│                                                                          │
│  Adding a new dealer:                                                    │
│    Insert one row into dealers (via Power Apps form)                     │
│    Insert one row into dealer_bindings                                   │
│    Master pipeline picks it up next minute. No code deployment.          │
│                                                                          │
│  Adding a new schema variant:                                            │
│    Author a new handler_notebook                                         │
│    Insert a new row into ingestion_patterns                              │
│    Reassign affected dealer_bindings to the new pattern_name              │
│    Old pattern stays runnable until all bindings migrate                  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## What you gain

| Property                                       | Solution A          | Solution C (Fabric)                                |
| ---------------------------------------------- | ------------------- | --------------------------------------------------- |
| Single platform for storage, compute, BI, ops | No                  | **Yes — OneLake unifies everything**                |
| Onboarding a new dealer                         | Code + deploy       | **Power Apps form submission, no deploy**           |
| Multi-tenant isolation (network + identity)     | Documented (ADR-011) | **Built-in via Workspaces + Entra ID groups**       |
| Lineage from source to Power BI report          | Not yet wired       | **Microsoft Purview tracks every hop**              |
| Power BI on the same data                       | Separate sync       | **Direct Lake — zero-copy semantic model**          |
| Real-time analytics (KQL)                       | PG `LISTEN/NOTIFY`  | **Eventhouse — sub-second KQL over millions of events** |
| Governance and compliance                        | Manual              | **Built-in sensitivity labels, DLP, audit**         |
| Total operational surface                        | 5+ open-source pieces | **One Fabric tenant**                              |

---

## What you give up

- **Vendor lock-in.** OneLake, Direct Lake, Eventhouse, Dataverse are Microsoft-specific. Migration to another platform is a multi-month project.
- **Capacity Unit (CU) pricing model.** Fabric bills by capacity reservation, not consumption. F2 (smallest paid SKU) starts around $260/month. Cost-effective at scale; expensive for pilots.
- **Closed-source compute.** Cannot inspect or modify Spark internals; debugging deep performance issues sometimes hits a vendor support ticket.
- **Restricted Notebook ecosystem.** Some PyPI packages are not whitelisted; custom kernels are unavailable.
- **Smaller talent market than AWS or pure OSS** outside enterprise Microsoft shops.

---

## When to choose Solution C

Adopt when **three or more** of these hold:

1. The organisation already standardises on Microsoft 365, Entra ID, and Power BI
2. Dealer onboarding velocity is a business constraint (no-code onboarding is the differentiator)
3. Multi-tenant governance and compliance are contractual requirements
4. Power BI semantic models are the primary consumer of catalog data
5. The team prefers fewer integration surfaces over maximum tool freedom
6. Procurement prefers a single Microsoft enterprise agreement over many SaaS contracts

If only one or two hold, Solution A or B is simpler. If high read scale with global SEO is the primary driver, consider an alternative beyond this submission (Cloudflare edge stack).

---

## How v10 control plane scales

The same generic Data Factory pipeline that handles 10 dealers handles 10,000. The lookup loop iterates over more rows; the dispatch activity is parallelised by Fabric Spark. No code is added per dealer.

| Dealers       | Operational change                                                    |
| ------------- | --------------------------------------------------------------------- |
| 1 to 50       | F4 capacity ($520/month), single Lakehouse                            |
| 50 to 500     | F8 ($1040/month), partition Lakehouse by `dealer_id`                  |
| 500 to 5,000  | F16 ($2080/month), introduce per-tier Lakehouses (Enterprise tier separate) |
| 5,000+        | F32+, multi-region deployment, Purview Federated Catalog               |

Compared to Solution A, Solution C's per-dealer marginal cost is higher at small scale and lower at large scale. Break-even is typically around 200 dealers.

---

## Implementation budget (AI-assisted estimate)

| Component                                            | Effort      |
| ---------------------------------------------------- | ----------- |
| Fabric workspace + capacity provisioning             | 2 hours     |
| OneLake Lakehouse bronze / silver / gold schema      | 4 hours     |
| Dataverse control plane tables + relationships       | 4 hours     |
| Master Data Factory pipeline (metadata-driven)       | 8 hours     |
| Three handler notebooks (one per pattern type)       | 12 hours    |
| Eventstream + Eventhouse streaming pipeline          | 6 hours     |
| Power BI semantic model with Direct Lake             | 4 hours     |
| Power Apps dealer-onboarding form                    | 4 hours     |
| Activator rules for low-stock + failure notifications | 2 hours     |
| Microsoft Purview integration and lineage validation | 4 hours     |
| Documentation + ADR series                            | 4 hours     |

**Total: approximately 54 hours AI-assisted, or three to four weeks manual.**

For an engineer with prior Fabric production experience, the metadata-driven pipeline is the load-bearing piece. The remaining components are integration work that the platform already accommodates.

---

## How Solution C relates to A and B

Solution C is **not a migration target from Solution A or B**. It is an **alternative starting point** for organisations whose technology baseline is already Microsoft.

If a team has built Solution A and reached the migration-trigger conditions (ADR-009), the natural next step is Solution B (Polars + Iceberg + Dagster on OSS) rather than Solution C. Migrating from open-source PostgreSQL + Redis to Microsoft Fabric is a discontinuous re-platforming, not an incremental scale-up.

Solution C exists in this submission as a documented alternative for completeness: the same business problem has different best answers depending on the organisation's strategic platform commitments.
