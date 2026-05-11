/**
 * BullMQ queue definitions and shared connection.
 *
 * One Redis connection is shared across all queues to minimise socket
 * count. Each queue has its own configured concurrency, retry policy,
 * and rate limit — heavy/slow jobs (parse-file) use low concurrency;
 * fast jobs (upload-image) use high concurrency + rate-limit.
 *
 * Streaming queues (stream-*) are defined here for completeness but only
 * wired up when STREAMING_ENABLED=true.
 */
import { Queue, type QueueOptions, type WorkerOptions } from "bullmq";
import IORedis from "ioredis";
import { env } from "../lib/env.js";

// BullMQ disallows ':' in queue names (it's their internal key separator).
export const QUEUE_NAMES = {
  parseFile: "parse-file",
  parseSheet: "parse-sheet",
  uploadImage: "upload-image",
  enrichLlm: "enrich-llm",
  streamInventory: "stream-inventory",
  streamPricing: "stream-pricing",
  streamOrder: "stream-order",
  dlq: "dlq",
} as const;

/** Shared Redis connection. BullMQ recommends one IORedis instance per queue/worker pair, but we deliberately share for simpler resource accounting. */
export const redis = new IORedis(env.REDIS_URL, {
  maxRetriesPerRequest: null, // required by BullMQ
  enableReadyCheck: false,
});

const defaultQueueOpts: QueueOptions = {
  connection: redis,
  defaultJobOptions: {
    attempts: 3,
    backoff: { type: "exponential", delay: 2000 },
    removeOnComplete: { age: 60 * 60 * 24, count: 5000 }, // keep 5k jobs, 24h
    removeOnFail: { age: 60 * 60 * 24 * 7 }, // keep failed 7 days for DLQ replay
  },
};

export const parseFileQueue = new Queue(QUEUE_NAMES.parseFile, defaultQueueOpts);
export const parseSheetQueue = new Queue(QUEUE_NAMES.parseSheet, defaultQueueOpts);
export const uploadImageQueue = new Queue(QUEUE_NAMES.uploadImage, defaultQueueOpts);
export const enrichLlmQueue = new Queue(QUEUE_NAMES.enrichLlm, defaultQueueOpts);

export const streamInventoryQueue = new Queue(QUEUE_NAMES.streamInventory, defaultQueueOpts);
export const streamPricingQueue = new Queue(QUEUE_NAMES.streamPricing, defaultQueueOpts);
export const streamOrderQueue = new Queue(QUEUE_NAMES.streamOrder, defaultQueueOpts);

/** Worker concurrency profile per queue. Adjust based on bottleneck profiling. */
export const WORKER_CONCURRENCY: Record<string, number> = {
  [QUEUE_NAMES.parseFile]: 1, // one xlsx at a time; fan-out internally
  [QUEUE_NAMES.parseSheet]: 8,
  [QUEUE_NAMES.uploadImage]: 16,
  [QUEUE_NAMES.enrichLlm]: 4,
  [QUEUE_NAMES.streamInventory]: 32, // light + fast
  [QUEUE_NAMES.streamPricing]: 16,
  [QUEUE_NAMES.streamOrder]: 16,
};

/** Common worker options reused by all worker entrypoints. */
export const workerOptions = (queueName: string): WorkerOptions => ({
  connection: redis,
  concurrency: WORKER_CONCURRENCY[queueName] ?? 4,
  // Rate-limit upload-image to stay under Cloudflare R2's 1000 req/sec cap.
  // Other queues default-unlimited; tune per worker via override.
  ...(queueName === QUEUE_NAMES.uploadImage
    ? { limiter: { max: 800, duration: 1000 } }
    : {}),
});

export async function closeQueues(): Promise<void> {
  await Promise.all([
    parseFileQueue.close(),
    parseSheetQueue.close(),
    uploadImageQueue.close(),
    enrichLlmQueue.close(),
    streamInventoryQueue.close(),
    streamPricingQueue.close(),
    streamOrderQueue.close(),
  ]);
  await redis.quit();
}

/* ───────────────────────────────────────────────────────────────────
 * Job payload typings — kept here so producers + workers share contracts
 * ───────────────────────────────────────────────────────────────────*/

export interface ParseFileJob {
  runId: string;
  dealerId: string | null;
  filePath: string;
  fileSha256: string;
}

export interface ParseSheetJob {
  runId: string;
  dealerId: string | null;
  filePath: string;
  fileSha256: string;
  sheetName: string;
  /** Filter applied at fanout — null means no filter. */
  rowLimit?: number | null;
}

export interface UploadImageJob {
  runId: string;
  filePath: string;
  imageFile: string; // bare filename inside xl/media/
  productIds: number[]; // products to link
  sectionLabel?: string | null;
  sourceSheet?: string | null;
}

export interface EnrichLlmJob {
  runId: string;
  productId: number;
  field: "name_en" | "callouts_verify";
  inputCn?: string | null;
  imageR2Key?: string | null;
}

export interface StreamEventJob {
  eventId: string;
  dealerId: string;
  eventType: "inventory" | "pricing" | "order";
  payload: Record<string, unknown>;
  source?: string | null;
}
