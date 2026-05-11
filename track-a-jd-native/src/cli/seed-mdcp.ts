#!/usr/bin/env node
/**
 * Seed the metadata-driven control plane (MDCP) tables with one realistic
 * example per table so the schema isn't documented-but-empty.
 *
 * The three MDCP tables (per ADR-014):
 *   dealers                     — tenants
 *   ingestion_patterns          — handler registry
 *   dealer_pattern_bindings     — per-tenant binding + per-binding overrides
 *
 * Production runtime: a control-loop process scans bindings, decides
 * whether each is due (cron-smart skip via last_run_sha256), and dispatches
 * to the handler registered in ingestion_patterns.handler_module. The
 * loop itself is the next milestone — this seed lets the tables and
 * relationships be inspected.
 *
 * Idempotent.
 *
 * Usage:  pnpm seed:mdcp
 */
import { sql, closeDb } from "../storage/db/client.js";
import { logger } from "../lib/logger.js";

async function main(): Promise<void> {
  const log = logger.child({ cli: "seed-mdcp" });

  // Three example patterns covering distinct ingestion modes (ADR-014).
  const patterns = [
    {
      name: "xlsx_oem_catalog_v1",
      type: "FILE_BATCH",
      handler: "ingest.handlers.xlsx_oem_catalog",
      signature: {
        sheet_filter: { kind: ["chassis", "engine", "chassis_u8"] },
        required_columns: ["No.", "EN name", "CN name"],
      },
      rules: { variant_dedupe: "by_part_number", null_policy: "skip" },
      sla: "PT24H",
      schedule: "on-source-change",
    },
    {
      name: "lightspeed_inventory_webhook_v1",
      type: "API_PUSH",
      handler: "streaming.handlers.lightspeed_inventory",
      signature: {
        body_schema: { part_number: "string", stock_level: "int", timestamp: "iso8601" },
      },
      rules: { reject_negative_stock: true, dedupe_window_sec: 5 },
      sla: "PT5M",
      schedule: "event-driven",
    },
    {
      name: "postgres_cdc_to_marketplace_v1",
      type: "CDC",
      handler: "streaming.handlers.pg_outbox_to_redpanda",
      signature: { topic_prefix: "postgres.cdc.", source_table: "stream_outbox" },
      rules: { batch_size: 100, max_lag_sec: 30 },
      sla: "PT1M",
      schedule: "event-driven",
    },
  ] as const;

  for (const p of patterns) {
    await sql`
      INSERT INTO ingestion_patterns
        (pattern_name, pattern_type, handler_module, schema_signature, validation_rules,
         default_freshness_sla, default_schedule, version)
      VALUES (${p.name}, ${p.type}, ${p.handler},
              ${JSON.stringify(p.signature)}::jsonb,
              ${JSON.stringify(p.rules)}::jsonb,
              ${p.sla}, ${p.schedule}, 1)
      ON CONFLICT (pattern_name) DO NOTHING
    `;
  }
  log.info({ patterns: patterns.length }, "ingestion_patterns seeded");

  // One example dealer (Kayo OEM — the source of the test xlsx).
  const dealer = await sql<Array<{ id: string }>>`
    INSERT INTO dealers
      (name, status, inferred_make, contact_email, tier)
    VALUES
      ('Kayo OEM Demo Dealer', 'ACTIVE', 'Kayo', 'demo@inventoryflow.ai', 'standard')
    ON CONFLICT DO NOTHING
    RETURNING id
  `;

  // If the INSERT was a no-op (existing row), fetch the id.
  const dealerId = dealer[0]?.id ?? (
    await sql<Array<{ id: string }>>`
      SELECT id FROM dealers WHERE name='Kayo OEM Demo Dealer' LIMIT 1
    `
  )[0]?.id;

  if (!dealerId) {
    throw new Error("Failed to upsert seed dealer");
  }

  // Bind all three patterns to that dealer with sane per-binding params.
  const bindings = [
    {
      pattern: "xlsx_oem_catalog_v1",
      params: { source_glob: "s3://catalog-raw/kayo/*.xlsx", priority: "high" },
      sla: "PT24H",
      schedule: "on-source-change",
    },
    {
      pattern: "lightspeed_inventory_webhook_v1",
      params: { webhook_secret_ref: "secrets/kayo/lightspeed" },
      sla: "PT5M",
      schedule: "event-driven",
    },
    {
      pattern: "postgres_cdc_to_marketplace_v1",
      params: { target_topic: "marketplace.kayo.inventory" },
      sla: "PT1M",
      schedule: "event-driven",
    },
  ];

  for (const b of bindings) {
    await sql`
      INSERT INTO dealer_pattern_bindings
        (dealer_id, pattern_name, params, freshness_sla, schedule, enabled)
      VALUES (${dealerId}::uuid, ${b.pattern}, ${JSON.stringify(b.params)}::jsonb,
              ${b.sla}, ${b.schedule}, true)
      ON CONFLICT (dealer_id, pattern_name) DO NOTHING
    `;
  }
  log.info({ dealerId, bindings: bindings.length }, "dealer + bindings seeded");
}

main()
  .then(async () => {
    await closeDb();
    process.exit(0);
  })
  .catch(async (err: unknown) => {
    logger.error({ err }, "seed-mdcp failed");
    await closeDb().catch(() => {});
    process.exit(1);
  });
