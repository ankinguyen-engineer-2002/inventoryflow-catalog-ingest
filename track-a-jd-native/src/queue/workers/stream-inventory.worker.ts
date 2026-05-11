/**
 * Stream inventory worker (ADR-010).
 *
 * Consumes inventory.changes events:
 *   1. Find the matching product by part_number_norm + dealer_id.
 *   2. Update products.data_quality JSONB with the new stock level
 *      (we don't introduce a separate stock_level column yet — that's a
 *      schema decision for Day 4 once consumer requirements firm up).
 *   3. Mark stream_events.status = PROCESSED.
 *   4. Emit a PG NOTIFY on channel "inventory_change" so the catalog API
 *      / marketplace sync worker can react in-process.
 *
 * SLA: <500ms p95 from webhook arrival to NOTIFY fired.
 */
import { Worker } from "bullmq";
import { eq, sql as drizzleSql } from "drizzle-orm";
import {
  QUEUE_NAMES,
  workerOptions,
  type StreamEventJob,
} from "../queues.js";
import { db, sql } from "../../storage/db/client.js";
import { streamEvents } from "../../storage/db/schema.js";
import { logger } from "../../lib/logger.js";

export const streamInventoryWorker = new Worker<StreamEventJob>(
  QUEUE_NAMES.streamInventory,
  async (job) => {
    const { eventId, dealerId, payload } = job.data;
    const partNumber = String(payload["part_number"]);
    const stockLevel = Number(payload["stock_level"]);
    const partNumberNorm = partNumber.toUpperCase().replace(/\s+/g, "");

    const log = logger.child({
      worker: "stream-inventory",
      eventId,
      partNumber,
    });

    // Update product if it exists (silent no-op if not — we don't auto-create).
    // Drop into raw SQL — Drizzle's typed update + complex JSONB merge is
    // awkward when half the values are typed bindings and half are inline
    // function calls.
    const updated = await sql<Array<{ id: number }>>`
      UPDATE products
      SET data_quality = coalesce(data_quality, '{}'::jsonb)
                         || jsonb_build_object(
                              'stock_level', ${stockLevel}::int,
                              'stock_updated_at', now()::text
                            ),
          updated_at = now()
      WHERE part_number_norm = ${partNumberNorm}
      RETURNING id
    `;

    await db
      .update(streamEvents)
      .set({
        status: updated.length ? "PROCESSED" : "FAILED",
        processedAt: drizzleSql`now()`,
        ...(updated.length === 0 ? { error: "product_not_found" } : {}),
      })
      .where(eq(streamEvents.eventId, eventId));

    if (updated.length) {
      // PG LISTEN/NOTIFY for in-process consumers (catalog API, marketplace sync).
      const payload = JSON.stringify({
        eventId,
        dealerId,
        partNumber,
        productId: updated[0]!.id,
        stockLevel,
      });
      // Use sql.unsafe to bind a typed parameter to pg_notify (which doesn't
      // accept implicit-type params via postgres.js tagged templates).
      await sql.unsafe(`SELECT pg_notify('inventory_change', $1::text)`, [payload]);
    }

    log.info({ productsUpdated: updated.length }, "inventory event processed");
    return { productsUpdated: updated.length };
  },
  workerOptions(QUEUE_NAMES.streamInventory),
);

streamInventoryWorker.on("failed", (job, err) => {
  logger.error({ jobId: job?.id, err }, "stream-inventory failed");
});
