#!/usr/bin/env node
/**
 * Populate vehicle_models from products.fitment.
 *
 * vehicle_models is the normalised dimension table for analytics joins and
 * downstream DQ ("does fitment.model_code exist in vehicle_models?").
 * Rather than build it during the main ingest path — where the same vehicle
 * surfaces on hundreds of rows and we'd hammer the unique index — derive
 * it as a post-pass with DISTINCT.
 *
 * Idempotent via ON CONFLICT.
 *
 * Usage:  pnpm populate:models
 */
import { sql, closeDb } from "../storage/db/client.js";
import { logger } from "../lib/logger.js";

async function main(): Promise<void> {
  const log = logger.child({ cli: "populate-vehicle-models" });
  log.info("Deriving vehicle_models from products.fitment...");

  // Unnest the fitment JSONB array into rows, group by identity tuple,
  // upsert. Year=0 (sentinel for "open-ended / unknown") becomes year_start
  // null so range queries work naturally.
  const result = await sql<Array<{ inserted: number }>>`
    WITH unnested AS (
      SELECT DISTINCT
        coalesce(elem->>'make', 'Unknown')                AS make,
        coalesce(elem->>'model', elem->>'model_code', 'Unknown') AS model,
        elem->>'model_code'                                AS model_code,
        elem->>'category'                                  AS category,
        NULLIF((elem->>'year')::int, 0)                    AS year,
        elem->>'variant'                                   AS variant
      FROM products,
           jsonb_array_elements(fitment) AS elem
      WHERE elem ? 'make' AND elem ? 'model'
    ),
    inserted AS (
      INSERT INTO vehicle_models
        (make, model, model_code, category, year_start, year_end, variant)
      SELECT make, model, model_code, category, year, year, variant
      FROM unnested
      ON CONFLICT (make, model_code, year_start, year_end, variant)
        DO NOTHING
      RETURNING 1
    )
    SELECT count(*)::int AS inserted FROM inserted
  `;

  const totalRow = await sql<Array<{ count: number }>>`SELECT count(*)::int AS count FROM vehicle_models`;

  log.info(
    {
      inserted_this_run: result[0]?.inserted ?? 0,
      total_in_table: totalRow[0]?.count ?? 0,
    },
    "vehicle_models populated",
  );
}

main()
  .then(async () => {
    await closeDb();
    process.exit(0);
  })
  .catch(async (err: unknown) => {
    logger.error({ err }, "populate failed");
    await closeDb().catch(() => {});
    process.exit(1);
  });
