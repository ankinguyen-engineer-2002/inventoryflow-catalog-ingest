/**
 * Migration runner — invoked by `pnpm db:migrate`.
 *
 * Drizzle Kit emits SQL files into ./migrations; this script applies them
 * idempotently. Tracks applied migrations in the __drizzle_migrations__ table.
 */
import { migrate } from "drizzle-orm/postgres-js/migrator";
import { db, closeDb } from "./client.js";
import { logger } from "../../lib/logger.js";

async function main(): Promise<void> {
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
