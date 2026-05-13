/**
 * PostgreSQL connection — singleton pool + Drizzle ORM.
 *
 * `postgres` (postgres.js) is preferred over `pg` for ESM-native +
 * smaller bundle. Drizzle has first-class support for both.
 */
import postgres from "postgres";
import { drizzle, type PostgresJsDatabase } from "drizzle-orm/postgres-js";
import { env } from "../../lib/env.js";
import { logger } from "../../lib/logger.js";
import * as schema from "./schema.js";

const client = postgres(env.DATABASE_URL, {
  max: env.DB_POOL_SIZE,
  idle_timeout: 30,
  connect_timeout: 5,
  prepare: false, // PgBouncer-compatible default
  onnotice: (n) => logger.debug({ notice: n }, "pg notice"),
});

export const db: PostgresJsDatabase<typeof schema> = drizzle(client, { schema });

/**
 * Type for either the singleton `db` client OR a transaction handle
 * obtained via `db.transaction(async (tx) => ...)`. Use this in
 * repository function signatures that should accept a tx so callers
 * can compose atomic batches without the repo silently dropping out
 * of the transaction.
 */
export type DbClient = PostgresJsDatabase<typeof schema> | Parameters<Parameters<typeof db.transaction>[0]>[0];

export async function closeDb(): Promise<void> {
  await client.end({ timeout: 5 });
}

// Convenience export for raw queries / LISTEN/NOTIFY where Drizzle abstraction is inconvenient.
export const sql = client;
