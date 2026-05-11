# ADR-014: Metadata-driven control plane (MDCP)

## Status
Accepted — 2026-05-11 · **Highest-priority addition for v10 senior-grade**

## Context

The JD explicitly states: *"Designing systems that can onboard hundreds of dealerships efficiently."* This is the most important architectural signal in the entire job description — and the original plan addressed it implicitly via per-dealer `rules.yaml` files. That's not sufficient at scale.

A senior-grade v10 control plane in 2026 is **metadata-driven**: pipeline behavior is defined by data (rows in tables, config files in version control) rather than by code (per-dealer modules, hardcoded handlers). Adding a new dealer becomes an `INSERT`, not a deploy.

The pattern is industry-standard:
- **Microsoft Fabric**: metadata-driven ingestion framework (config tables drive Copy activities).
- **Airbyte**: source connectors registered in a catalog; new sources = config additions.
- **Meltano / dlt-hub**: declarative pipelines defined in YAML, dispatched by a generic engine.
- **Databricks DLT**: declarative table definitions; the engine handles orchestration.
- **dbt**: declarative model definitions; the engine handles dependency resolution.

Combined with **freshness-based scheduling** (run only when data is stale or upstream changed, not blind cron) and **multi-pattern dispatch** (file batch, API pull, webhook push, CDC, streaming all served by one engine), this is what distinguishes a senior-engineering submission from a junior one.

## Decision

Adopt a metadata-driven control plane with three coordinated registries plus a generic dispatch engine. Both tracks share the metadata schema (Postgres tables); Track B additionally exposes the metadata through Dagster's asset model.

### 1. The three registries

```sql
-- ============================================================
-- Registry 1: Dealers (the tenants)
-- ============================================================
CREATE TABLE dealers (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name              TEXT NOT NULL,
  status            TEXT NOT NULL,             -- 'ACTIVE'|'PAUSED'|'OFFBOARDED'
  inferred_make     TEXT,                       -- 'Kayo'|'Polaris'|...
  contact_email     TEXT,
  onboarded_at      TIMESTAMPTZ DEFAULT now(),
  metadata          JSONB DEFAULT '{}'::jsonb   -- arbitrary per-dealer attrs
);

-- ============================================================
-- Registry 2: Ingestion patterns (the handlers)
-- ============================================================
CREATE TABLE ingestion_patterns (
  pattern_name      TEXT PRIMARY KEY,          -- 'xlsx_oem_catalog_v1', 'lightspeed_webhook_v2'
  pattern_type      TEXT NOT NULL,             -- 'FILE_BATCH'|'API_PULL'|'API_PUSH'|'CDC'|'STREAM_CONSUMER'
  handler_module    TEXT NOT NULL,             -- 'ingest.handlers.xlsx_oem_catalog'
  schema_signature  JSONB NOT NULL,            -- expected column set, types
  validation_rules  JSONB NOT NULL,            -- Zod schema or JSON Schema reference
  default_freshness_sla TEXT,                  -- ISO 8601 duration: 'PT24H' = 24h
  default_schedule  TEXT,                       -- cron expr OR 'event-driven' OR 'on-source-change'
  version           INT NOT NULL,
  deprecated_at     TIMESTAMPTZ,
  created_at        TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- Registry 3: Dealer ↔ Pattern bindings (the assignments)
-- ============================================================
CREATE TABLE dealer_pattern_bindings (
  id                BIGSERIAL PRIMARY KEY,
  dealer_id         UUID NOT NULL REFERENCES dealers(id),
  pattern_name      TEXT NOT NULL REFERENCES ingestion_patterns(pattern_name),
  params            JSONB NOT NULL,            -- per-binding overrides (cron, freshness, source URL)
  freshness_sla     TEXT,                       -- override of pattern default
  schedule          TEXT,                       -- override of pattern default
  enabled           BOOLEAN NOT NULL DEFAULT true,
  last_run_id       UUID REFERENCES ingest_runs(run_id),
  last_run_sha256   TEXT,                       -- for cron-smart skip
  last_run_at       TIMESTAMPTZ,
  created_at        TIMESTAMPTZ DEFAULT now(),
  UNIQUE (dealer_id, pattern_name)
);

CREATE INDEX idx_bindings_enabled_due
  ON dealer_pattern_bindings (enabled, last_run_at);
```

### 2. The generic dispatch engine

```
                       ┌────────────────────────────────────┐
                       │ CONTROL LOOP (runs every minute)   │
                       │   FOR EACH binding WHERE enabled:  │
                       │     evaluate_should_run(binding)   │
                       │     IF yes:                         │
                       │       dispatch_to_handler(binding) │
                       └────────────────────────────────────┘
                                       │
                       ┌───────────────┴────────────────────┐
                       │ evaluate_should_run logic:         │
                       │  1. If pattern is event-driven →   │
                       │       wait for event (no schedule) │
                       │  2. If schedule is 'on-source-     │
                       │       change' → check SHA-256;     │
                       │       skip if unchanged            │
                       │  3. If cron → match current minute │
                       │  4. Always check freshness SLA:    │
                       │       if last_run_at + sla < now() │
                       │       force-run regardless         │
                       └────────────────────────────────────┘
                                       │
                                       ▼
                       ┌────────────────────────────────────┐
                       │ dispatch_to_handler:               │
                       │   handler = import(pattern         │
                       │              .handler_module)      │
                       │   handler.execute(binding.params)  │
                       │   on success: update last_run_*    │
                       │   on failure: enqueue DLQ          │
                       └────────────────────────────────────┘
```

### 3. Multi-pattern handlers

Every pattern is a self-contained handler module implementing a small interface:

```ts
// Track A — TypeScript
interface IIngestionHandler {
  readonly pattern_name: string;
  readonly pattern_type: 'FILE_BATCH' | 'API_PULL' | 'API_PUSH' | 'CDC' | 'STREAM_CONSUMER';
  execute(params: PatternParams, ctx: RunContext): Promise<RunResult>;
}

// Examples
export const xlsxOemCatalogHandler: IIngestionHandler = {
  pattern_name: 'xlsx_oem_catalog_v1',
  pattern_type: 'FILE_BATCH',
  async execute({ file_path, sheet_filter }, ctx) {
    // exceljs streaming, section detection, etc.
  },
};

export const lightspeedWebhookHandler: IIngestionHandler = {
  pattern_name: 'lightspeed_webhook_v2',
  pattern_type: 'API_PUSH',
  async execute({ event_payload }, ctx) {
    // Zod validate, UPSERT, NOTIFY
  },
};
```

For Track B, handlers are **Dagster assets** with the same interface contract:

```python
# Track B — Python (Dagster)
@asset(
    partitions_def=DynamicPartitionsDefinition(name="dealer"),
    freshness_policy=FreshnessPolicy(maximum_lag_minutes=60 * 24),  # 24h SLA
    auto_materialize_policy=AutoMaterializePolicy.eager(),
)
def bronze_catalog_rows(context, dealer_config: DealerConfig) -> pl.DataFrame:
    # Reads metadata, dispatches to handler, returns DataFrame
```

### 4. Freshness-based scheduling (cron-smart skip)

The control loop's `evaluate_should_run` is the key innovation. It replaces blind cron with three layered checks:

| Layer                  | Question asked                                              | If yes |
| ---------------------- | ----------------------------------------------------------- | ------ |
| 1. Event-driven        | Is this pattern triggered by external event (webhook)?      | Skip schedule check; wait for event |
| 2. Source-change check | Has the source SHA-256 changed since last run?              | Run; if no → skip with `reason='UNCHANGED_SOURCE'` |
| 3. Cron match          | Does current minute match cron expression?                  | Run |
| 4. Freshness SLA       | Has `last_run_at + sla` elapsed?                            | Force-run regardless of 1-3 (SLA wins) |

Skipped runs are still **logged** in `ingest_runs` with `status='SKIPPED'` + `reason` so operators can audit *why* a pipeline didn't fire.

For Track B, this is delivered by **Dagster's `AutoMaterializePolicy.eager()` + `FreshnessPolicy(maximum_lag_minutes=...)`** — same semantics, native to the framework.

## AI suggestion vs my override

**Claude initially suggested** keeping the per-dealer `rules.yaml` model and avoiding "premature abstraction".

**I overrode** because:

1. **The JD explicitly calls out** "Designing systems that can onboard hundreds of dealerships efficiently." That's not premature abstraction — that's the stated requirement.
2. **`rules.yaml` per dealer doesn't scale operationally** past ~20 dealers. Code reviews, deploys, secret management per file is friction.
3. **Metadata-driven is the 2025-2026 senior signal**. Submitting a per-dealer-yaml plan in 2026 reads as 2018-era thinking.
4. **The control plane TABLE schema *is* the abstraction**, not a generic engine that doesn't exist yet. The handlers are still concrete code; only the binding is data-driven.
5. **Freshness-based scheduling is non-optional at scale** — blind cron creates either wasted work (running when source hasn't changed) or stale data (waiting for cron tick when source has changed). One bad outcome at small scale; both at large scale.

## Trade-offs accepted

- **Three new tables to maintain** (dealers, ingestion_patterns, dealer_pattern_bindings). Mitigated: schema is stable; new patterns are rows, not migrations.
- **Indirection between code and behavior**: reading a binding row + finding the handler module is one extra hop vs direct code paths. Acceptable; the alternative is far worse at scale.
- **Schema evolution of `params` JSONB**: changes must be versioned per pattern (`xlsx_oem_catalog_v1` → `_v2`) to avoid breaking existing bindings. Documented in ADR-012 (data contracts).
- **PoC scope risk**: implementing all 7 pattern types in 5 days is infeasible. PoC ships `FILE_BATCH` + `API_PUSH` + (Track B) `CDC` — enough to prove the dispatch model; the other 4 patterns are stubbed with interface contract + "TODO" comments.

## When to revisit

- **If pattern count exceeds 20**: introduce a code-gen step so new patterns scaffold consistently.
- **If dealer count exceeds 1000**: pre-compute "due bindings" in a materialized view, refresh every minute, instead of full-table scan.
- **If audit needs cross-tenant queries**: switch `last_run_*` fields off `dealer_pattern_bindings` into a dedicated `binding_runs` table to preserve history.

## Sources

- Microsoft Fabric metadata-driven ingestion framework: https://learn.microsoft.com/en-us/azure/architecture/databases/idea/metadata-driven-pipelines (retrieved 2026-05-11). [Verified]
- Airbyte source catalog architecture: https://docs.airbyte.com/understanding-airbyte/airbyte-protocol (retrieved 2026-05-11). [Verified]
- Meltano declarative pipelines: https://docs.meltano.com/concepts/project (retrieved 2026-05-11). [Verified]
- dlt-hub source decorator pattern: https://dlthub.com/docs/general-usage/source (retrieved 2026-05-11). [Verified]
- Dagster `AutoMaterializePolicy` + `FreshnessPolicy`: https://docs.dagster.io/concepts/assets/asset-auto-execution (retrieved 2026-05-11). [Verified]
- dbt source freshness checks: https://docs.getdbt.com/docs/deploy/source-freshness (retrieved 2026-05-11). [Verified]
- "Outbox pattern" for transactional event publishing: https://microservices.io/patterns/data/transactional-outbox.html (retrieved 2026-05-11).
- Kleppmann, *Designing Data-Intensive Applications*, Ch. 10 ("Batch Processing") + Ch. 11 ("Stream Processing").
