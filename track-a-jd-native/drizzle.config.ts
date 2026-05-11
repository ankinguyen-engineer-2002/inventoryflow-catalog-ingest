import { defineConfig } from "drizzle-kit";

/**
 * Drizzle Kit config.
 * Generates SQL migrations from src/storage/db/schema.ts.
 *
 * Usage:
 *   pnpm db:generate          → emit new migration in ./migrations
 *   pnpm db:migrate           → apply pending migrations
 *   pnpm db:studio            → open visual editor
 */
export default defineConfig({
  schema: "./src/storage/db/schema.ts",
  out: "./migrations",
  dialect: "postgresql",
  dbCredentials: {
    url:
      process.env["DATABASE_URL"] ??
      "postgres://dev:dev@localhost:5432/catalog",
  },
  verbose: true,
  strict: true,
});
