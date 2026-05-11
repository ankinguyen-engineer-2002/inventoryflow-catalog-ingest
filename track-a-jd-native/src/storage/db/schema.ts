/**
 * Drizzle schema — single source of truth for the database structure.
 *
 * Migrations are generated from this file:
 *   pnpm db:generate
 *
 * Then applied with:
 *   pnpm db:migrate
 *
 * Tables (11 total, grouped by purpose):
 *   • Catalog core: products, product_images, part_number_aliases, vehicle_models
 *   • Runs + audit: ingest_runs, ingest_audit
 *   • Streaming:    stream_events, stream_outbox
 *   • MDCP:         dealers, ingestion_patterns, dealer_pattern_bindings
 *   • Reference:    reference_specs
 *
 * See PLAN.md §8 for the schema design rationale + ADRs for trade-offs.
 */
import { sql } from "drizzle-orm";
import {
  bigint,
  bigserial,
  boolean,
  index,
  integer,
  jsonb,
  numeric,
  pgTable,
  primaryKey,
  text,
  timestamp,
  uniqueIndex,
  uuid,
} from "drizzle-orm/pg-core";

/* ───────────────────────────────────────────────────────────────────
 * Catalog core
 * ───────────────────────────────────────────────────────────────────*/

/**
 * Vehicle dimension table.
 * One row per (make, model_code, year_start, year_end, variant) tuple.
 * Used for fitment normalization + DQ validation against products.fitment[].
 */
export const vehicleModels = pgTable(
  "vehicle_models",
  {
    id: bigserial("id", { mode: "number" }).primaryKey(),
    make: text("make").notNull(),
    model: text("model").notNull(),
    modelCode: text("model_code"),
    category: text("category"), // 'SPORT_ATV' | 'UTILITY_ATV' | 'PITBIKE_EPA' | ...
    yearStart: integer("year_start"),
    yearEnd: integer("year_end"), // null for open-ended ("2023+")
    variant: text("variant"), // 'EPA' | 'EFI' | 'D' | null
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (t) => [
    uniqueIndex("ux_vehicle_models_identity").on(
      t.make,
      t.modelCode,
      t.yearStart,
      t.yearEnd,
      t.variant,
    ),
    index("ix_vehicle_models_category").on(t.category),
  ],
);

/**
 * Canonical product catalog. The crown jewel.
 *
 * fitment is a JSONB array of {year, make, model, model_code, category, section, callout_no}
 * objects. Indexed via GIN jsonb_path_ops for sub-50ms @> containment queries.
 *
 * See ADR-002 for the JSONB vs join-table reasoning.
 */
export const products = pgTable(
  "products",
  {
    id: bigserial("id", { mode: "number" }).primaryKey(),
    partNumber: text("part_number").notNull(),
    // Generated column — auto-normalised for case-insensitive uniqueness.
    partNumberNorm: text("part_number_norm").generatedAlwaysAs(
      sql`upper(regexp_replace(part_number, '\s', '', 'g'))`,
    ),
    nameEn: text("name_en"),
    nameCn: text("name_cn"),
    specCn: text("spec_cn"),
    qtyPerVehicle: numeric("qty_per_vehicle"),
    dealerCost: numeric("dealer_cost"),
    unit: text("unit"),
    retailPrice: numeric("retail_price"),
    /** See ADR-002. */
    fitment: jsonb("fitment").$type<FitmentEntry[]>().notNull().default(sql`'[]'::jsonb`),
    primaryImageR2Key: text("primary_image_r2_key"),
    sourceDealerId: uuid("source_dealer_id"),
    sourceFileSha256: text("source_file_sha256"),
    sourceSheet: text("source_sheet"),
    sourceRowIndex: integer("source_row_index"),
    /**
     * data_quality JSONB stores per-row provenance + DQ flags, eg:
     *   { name_en_source: 'llm_translated' | 'oem' | 'fallback',
     *     callouts_verified: boolean,
     *     llm_call_id: uuid }
     */
    dataQuality: jsonb("data_quality").$type<Record<string, unknown>>().default(sql`'{}'::jsonb`),
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (t) => [
    uniqueIndex("ux_products_partnum_dealer").on(t.partNumberNorm, t.sourceDealerId),
    index("ix_products_fitment_gin").using(
      "gin",
      sql`${t.fitment} jsonb_path_ops`,
    ),
    index("ix_products_name_en_trgm").using("gin", sql`${t.nameEn} gin_trgm_ops`),
    index("ix_products_name_cn_trgm").using("gin", sql`${t.nameCn} gin_trgm_ops`),
  ],
);

/** Many-to-many: a part appears in N schematic images. */
export const productImages = pgTable(
  "product_images",
  {
    productId: bigint("product_id", { mode: "number" })
      .notNull()
      .references(() => products.id, { onDelete: "cascade" }),
    r2Key: text("r2_key").notNull(),
    r2Url: text("r2_url").notNull(),
    sha256: text("sha256").notNull(),
    widthPx: integer("width_px"),
    heightPx: integer("height_px"),
    sectionLabel: text("section_label"),
    sourceSheet: text("source_sheet"),
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (t) => [primaryKey({ columns: [t.productId, t.sha256] })],
);

/**
 * Cross-reference: OLD/NEW part number history + distributor SKUs.
 * See ADR-006.
 */
export const partNumberAliases = pgTable(
  "part_number_aliases",
  {
    productId: bigint("product_id", { mode: "number" })
      .notNull()
      .references(() => products.id, { onDelete: "cascade" }),
    alias: text("alias").notNull(),
    aliasNorm: text("alias_norm").notNull(),
    aliasType: text("alias_type").notNull(), // 'old' | 'oem_alt' | 'distributor'
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (t) => [
    primaryKey({ columns: [t.aliasNorm, t.productId] }),
    index("ix_aliases_norm").on(t.aliasNorm),
  ],
);

/* ───────────────────────────────────────────────────────────────────
 * Runs + audit
 * ───────────────────────────────────────────────────────────────────*/

/** One row per ingest invocation. */
export const ingestRuns = pgTable(
  "ingest_runs",
  {
    runId: uuid("run_id").defaultRandom().primaryKey(),
    dealerId: uuid("dealer_id"),
    sourceFile: text("source_file").notNull(),
    sourceSha256: text("source_sha256").notNull(),
    status: text("status").notNull(), // QUEUED|RUNNING|SUCCESS|PARTIAL|FAILED|SKIPPED
    rowsAttempted: integer("rows_attempted"),
    rowsSucceeded: integer("rows_succeeded"),
    rowsFailed: integer("rows_failed"),
    llmCalls: integer("llm_calls"),
    llmCostUsd: numeric("llm_cost_usd", { precision: 10, scale: 4 }),
    startedAt: timestamp("started_at", { withTimezone: true }).defaultNow().notNull(),
    finishedAt: timestamp("finished_at", { withTimezone: true }),
    error: text("error"),
    reason: text("reason"), // e.g., 'UNCHANGED_SOURCE' when status=SKIPPED
  },
  (t) => [
    index("ix_runs_status_started").on(t.status, t.startedAt),
    index("ix_runs_dealer_started").on(t.dealerId, t.startedAt),
  ],
);

/** Every LLM call ever (and other audited external calls). */
export const ingestAudit = pgTable(
  "ingest_audit",
  {
    id: bigserial("id", { mode: "number" }).primaryKey(),
    runId: uuid("run_id").references(() => ingestRuns.runId),
    provider: text("provider").notNull(),
    promptSha256: text("prompt_sha256").notNull(),
    promptTemplateVer: text("prompt_template_ver").notNull(),
    responseText: text("response_text"),
    tokensIn: integer("tokens_in"),
    tokensOut: integer("tokens_out"),
    costUsd: numeric("cost_usd", { precision: 10, scale: 6 }),
    latencyMs: integer("latency_ms"),
    cacheHit: boolean("cache_hit").notNull(),
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (t) => [index("ix_audit_run_time").on(t.runId, t.createdAt)],
);

/* ───────────────────────────────────────────────────────────────────
 * Streaming (ADR-010)
 * ───────────────────────────────────────────────────────────────────*/

/** Stream event registry — every inbound webhook event. */
export const streamEvents = pgTable(
  "stream_events",
  {
    eventId: uuid("event_id").defaultRandom().primaryKey(),
    dealerId: uuid("dealer_id").notNull(),
    eventType: text("event_type").notNull(), // 'inventory' | 'pricing' | 'order' | ...
    payload: jsonb("payload").$type<Record<string, unknown>>().notNull(),
    source: text("source"), // 'lightspeed' | 'ebay' | 'amazon' | 'manual'
    receivedAt: timestamp("received_at", { withTimezone: true }).defaultNow().notNull(),
    processedAt: timestamp("processed_at", { withTimezone: true }),
    status: text("status").notNull(), // PENDING | PROCESSED | FAILED
    error: text("error"),
  },
  (t) => [
    index("ix_stream_events_dealer_status").on(t.dealerId, t.status, t.receivedAt),
  ],
);

/**
 * Outbox pattern — write events here atomically with business state;
 * a separate publisher drains to Redpanda/queue with at-least-once.
 */
export const streamOutbox = pgTable(
  "stream_outbox",
  {
    id: bigserial("id", { mode: "number" }).primaryKey(),
    topic: text("topic").notNull(),
    payload: jsonb("payload").$type<Record<string, unknown>>().notNull(),
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
    publishedAt: timestamp("published_at", { withTimezone: true }),
    status: text("status").notNull().default("PENDING"),
  },
  (t) => [
    index("ix_outbox_pending").on(t.createdAt).where(sql`status = 'PENDING'`),
  ],
);

/* ───────────────────────────────────────────────────────────────────
 * Metadata-driven control plane (ADR-014)
 * ───────────────────────────────────────────────────────────────────*/

/** Tenants. One row per dealer. */
export const dealers = pgTable("dealers", {
  id: uuid("id").defaultRandom().primaryKey(),
  name: text("name").notNull(),
  status: text("status").notNull(), // ACTIVE | PAUSED | OFFBOARDED
  inferredMake: text("inferred_make"),
  contactEmail: text("contact_email"),
  tier: text("tier").notNull().default("standard"), // free | standard | enterprise
  onboardedAt: timestamp("onboarded_at", { withTimezone: true }).defaultNow().notNull(),
  metadata: jsonb("metadata").$type<Record<string, unknown>>().default(sql`'{}'::jsonb`),
});

/** Handler registry — pattern_name → handler module + schema. */
export const ingestionPatterns = pgTable(
  "ingestion_patterns",
  {
    patternName: text("pattern_name").primaryKey(),
    patternType: text("pattern_type").notNull(), // FILE_BATCH | API_PULL | API_PUSH | CDC | STREAM_CONSUMER | STREAM_PRODUCER | DB_SNAPSHOT
    handlerModule: text("handler_module").notNull(),
    schemaSignature: jsonb("schema_signature").$type<Record<string, unknown>>().notNull(),
    validationRules: jsonb("validation_rules").$type<Record<string, unknown>>().notNull(),
    defaultFreshnessSla: text("default_freshness_sla"), // ISO 8601 duration
    defaultSchedule: text("default_schedule"), // cron | 'event-driven' | 'on-source-change'
    version: integer("version").notNull(),
    deprecatedAt: timestamp("deprecated_at", { withTimezone: true }),
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  },
);

/** Dealer ↔ pattern assignments — drives dispatch. */
export const dealerPatternBindings = pgTable(
  "dealer_pattern_bindings",
  {
    id: bigserial("id", { mode: "number" }).primaryKey(),
    dealerId: uuid("dealer_id")
      .notNull()
      .references(() => dealers.id, { onDelete: "cascade" }),
    patternName: text("pattern_name")
      .notNull()
      .references(() => ingestionPatterns.patternName),
    params: jsonb("params").$type<Record<string, unknown>>().notNull(),
    freshnessSla: text("freshness_sla"), // override pattern default
    schedule: text("schedule"), // override pattern default
    enabled: boolean("enabled").notNull().default(true),
    lastRunId: uuid("last_run_id").references(() => ingestRuns.runId),
    lastRunSha256: text("last_run_sha256"), // for cron-smart skip
    lastRunAt: timestamp("last_run_at", { withTimezone: true }),
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (t) => [
    uniqueIndex("ux_bindings_dealer_pattern").on(t.dealerId, t.patternName),
    index("ix_bindings_enabled_lastrun").on(t.enabled, t.lastRunAt),
  ],
);

/* ───────────────────────────────────────────────────────────────────
 * Reference specs (the ~12 weird sheets)
 * ───────────────────────────────────────────────────────────────────*/

export const referenceSpecs = pgTable(
  "reference_specs",
  {
    id: bigserial("id", { mode: "number" }).primaryKey(),
    category: text("category").notNull(), // 'carburetor_jets' | 'spark_plugs' | ...
    modelCode: text("model_code"),
    attributes: jsonb("attributes").$type<Record<string, unknown>>().notNull(),
    sourceSheet: text("source_sheet"),
    sourceRow: integer("source_row"),
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (t) => [index("ix_refspecs_category_model").on(t.category, t.modelCode)],
);

/* ───────────────────────────────────────────────────────────────────
 * TypeScript types — exported for application code
 * ───────────────────────────────────────────────────────────────────*/

/** Shape of a single entry in products.fitment[] */
export interface FitmentEntry {
  year: number;
  make: string;
  model: string;
  model_code?: string | null;
  variant?: string | null;
  category?: string | null;
  section?: string | null;
  callout_no?: string | null;
  callout_verified?: boolean;
  confidence?: "high" | "medium" | "low";
}

export type Product = typeof products.$inferSelect;
export type NewProduct = typeof products.$inferInsert;
export type ProductImage = typeof productImages.$inferSelect;
export type NewProductImage = typeof productImages.$inferInsert;
export type IngestRun = typeof ingestRuns.$inferSelect;
export type NewIngestRun = typeof ingestRuns.$inferInsert;
export type IngestAudit = typeof ingestAudit.$inferSelect;
export type StreamEvent = typeof streamEvents.$inferSelect;
export type Dealer = typeof dealers.$inferSelect;
export type IngestionPattern = typeof ingestionPatterns.$inferSelect;
export type DealerPatternBinding = typeof dealerPatternBindings.$inferSelect;
