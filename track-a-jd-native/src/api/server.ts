/**
 * Fastify HTTP server.
 *
 * Exposes:
 *   • Batch ingest:   POST /runs   (enqueue a file for processing)
 *   • Streaming:      POST /events/{inventory,pricing,order}
 *   • Health:         GET /healthz, GET /readyz
 *   • Metrics:        GET /metrics  (Prometheus exposition)
 *
 * Streaming routes are <500ms p95 SLA: validate → dispatch to stream
 * queue → 202 Accepted. No LLM in the stream hot path.
 */
import Fastify from "fastify";
import { env } from "../lib/env.js";
import { logger } from "../lib/logger.js";
import { healthRoutes } from "./routes/health.routes.js";
import { runsRoutes } from "./routes/runs.routes.js";
import { eventsRoutes } from "./routes/events.routes.js";
import { multitenantPlugin } from "./plugins/multitenant.plugin.js";

export async function buildServer(): Promise<ReturnType<typeof Fastify>> {
  const app = Fastify({
    loggerInstance: logger,
    trustProxy: true,
    bodyLimit: 2 * 1024 * 1024, // 2 MB — webhooks are small JSON
  });

  await app.register(multitenantPlugin);
  await app.register(healthRoutes);
  await app.register(runsRoutes);
  await app.register(eventsRoutes);

  return app;
}

async function main(): Promise<void> {
  const app = await buildServer();
  try {
    await app.listen({ port: env.APP_PORT, host: "0.0.0.0" });
    logger.info({ port: env.APP_PORT }, "API listening");
  } catch (err) {
    logger.error({ err }, "Failed to start server");
    process.exit(1);
  }
}

// Run only when invoked directly (not when imported by tests).
const isMain = process.argv[1]?.endsWith("server.ts") || process.argv[1]?.endsWith("server.js");
if (isMain) {
  void main();
}
