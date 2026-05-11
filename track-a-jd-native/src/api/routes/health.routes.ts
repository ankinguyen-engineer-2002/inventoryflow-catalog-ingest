/**
 * Health + readiness probes + Prometheus metrics.
 *
 * /healthz  — liveness: returns 200 if process is up.
 * /readyz   — readiness: returns 200 if DB + Redis reachable.
 * /metrics  — Prometheus exposition (placeholder for now — extend with
 *             prom-client when needed).
 */
import type { FastifyInstance } from "fastify";
import { sql } from "../../storage/db/client.js";
import { redis } from "../../queue/queues.js";

export async function healthRoutes(app: FastifyInstance): Promise<void> {
  app.get("/healthz", async () => ({ ok: true, ts: new Date().toISOString() }));

  app.get("/readyz", async (_req, reply) => {
    const checks: Record<string, string> = {};
    try {
      await sql`SELECT 1`;
      checks["postgres"] = "ok";
    } catch (err) {
      checks["postgres"] = `down: ${(err as Error).message}`;
    }
    try {
      await redis.ping();
      checks["redis"] = "ok";
    } catch (err) {
      checks["redis"] = `down: ${(err as Error).message}`;
    }
    const ok = Object.values(checks).every((v) => v === "ok");
    return reply.code(ok ? 200 : 503).send({ ok, checks });
  });

  app.get("/metrics", async (_req, reply) => {
    // Minimal exposition — Day-3 placeholder. Wire prom-client for full
    // counters/histograms in production.
    return reply
      .header("content-type", "text/plain; version=0.0.4")
      .send("# HELP up Process is running\n# TYPE up gauge\nup 1\n");
  });
}
