/**
 * Migration runner — invoked by `pnpm db:migrate`.
 *
 * Drizzle Kit emits SQL files into ./migrations; this script applies them
 * idempotently. Tracks applied migrations in the __drizzle_migrations__ table.
 */
import { migrate } from "drizzle-orm/postgres-js/migrator";
import { db, closeDb, sql } from "./client.js";
import { logger } from "../../lib/logger.js";

async function ensureExtensions(): Promise<void> {
  // pg_trgm + pgcrypto are required by schema.ts (gin_trgm_ops indexes,
  // gen_random_uuid() defaults). Docker-compose mounts scripts/postgres-init.sql
  // for local dev; CI uses the bare image, so we self-provision idempotently.
  await sql.unsafe("CREATE EXTENSION IF NOT EXISTS pgcrypto");
  await sql.unsafe("CREATE EXTENSION IF NOT EXISTS pg_trgm");
}

async function main(): Promise<void> {
  logger.info("Ensuring required PostgreSQL extensions...");
  await ensureExtensions();
  logger.info("Running migrations...");
  await migrate(db, { migrationsFolder: "./migrations" });
  logger.info("Migrations applied successfully.");
}

main()
  .then(() => closeDb())
  .then(() => process.exit(0))
  .catch((err) => {
    logger.error({ err }, "Migration failed");
    process.exit(1);
  });
