/**
 * Streaming event webhooks (ADR-010).
 *
 *   POST /events/inventory  — Lightspeed/dealer pushes stock-level change
 *   POST /events/pricing    — pricing update
 *   POST /events/order      — order placed
 *
 * Contract: 202 Accepted within p95 <500ms. The route validates the payload,
 * persists it to stream_events + stream_outbox in one transaction, and
 * enqueues a light-weight BullMQ job for downstream processing.
 */
import type { FastifyInstance, FastifyRequest, FastifyReply } from "fastify";
import { z } from "zod";
import type { Queue } from "bullmq";
import { db } from "../../storage/db/client.js";
import { streamEvents, streamOutbox } from "../../storage/db/schema.js";
import {
  streamInventoryQueue,
  streamPricingQueue,
  streamOrderQueue,
} from "../../queue/queues.js";

const InventoryEvent = z.object({
  part_number: z.string().min(1),
  stock_level: z.number().int().nonnegative(),
  warehouse_id: z.string().optional(),
  timestamp: z.string().datetime().optional(),
});

const PricingEvent = z.object({
  part_number: z.string().min(1),
  retail_price: z.number().nonnegative(),
  dealer_cost: z.number().nonnegative().optional(),
  currency: z.string().length(3).default("USD"),
  timestamp: z.string().datetime().optional(),
});

const OrderEvent = z.object({
  order_id: z.string().min(1),
  marketplace: z.string().min(1), // "ebay" | "amazon" | ...
  part_number: z.string().min(1),
  quantity: z.number().int().positive(),
  timestamp: z.string().datetime().optional(),
});

export async function eventsRoutes(app: FastifyInstance): Promise<void> {
  app.post("/events/inventory", async (req, reply) => {
    return handleEvent(req, reply, "inventory", InventoryEvent, streamInventoryQueue);
  });

  app.post("/events/pricing", async (req, reply) => {
    return handleEvent(req, reply, "pricing", PricingEvent, streamPricingQueue);
  });

  app.post("/events/order", async (req, reply) => {
    return handleEvent(req, reply, "order", OrderEvent, streamOrderQueue);
  });
}

async function handleEvent(
  req: FastifyRequest,
  reply: FastifyReply,
  eventType: "inventory" | "pricing" | "order",
  schema: z.ZodTypeAny,
  queue: Queue,
): Promise<unknown> {
  if (!req.dealerId) {
    return reply.code(401).send({ error: "missing_dealer_id" });
  }

  const parsed = schema.safeParse(req.body);
  if (!parsed.success) {
    return reply.code(400).send({ error: "invalid_event", issues: parsed.error.issues });
  }

  const eventId = crypto.randomUUID();
  const dealerId = req.dealerId;

  // Atomic write: stream_events + stream_outbox in one transaction.
  await db.transaction(async (tx) => {
    await tx.insert(streamEvents).values({
      eventId,
      dealerId,
      eventType,
      payload: parsed.data,
      source: getSource(req.headers),
      status: "PENDING",
    });
    await tx.insert(streamOutbox).values({
      topic: `${eventType}.changes`,
      payload: { eventId, dealerId, eventType, ...parsed.data },
    });
  });

  // Enqueue BullMQ stream job for in-process worker pickup.
  await queue.add(
    eventType,
    {
      eventId,
      dealerId,
      eventType,
      payload: parsed.data,
      source: getSource(req.headers),
    },
    { jobId: eventId },
  );

  return reply.code(202).send({ eventId, accepted: true });
}

function getSource(headers: Record<string, unknown>): string | null {
  const raw = headers["x-source"];
  if (typeof raw === "string") return raw;
  if (Array.isArray(raw)) return raw[0] ?? null;
  return null;
}
