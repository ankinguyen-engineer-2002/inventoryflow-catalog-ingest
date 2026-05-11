/**
 * Worker bootstrap entry.
 *
 * `pnpm worker` starts ALL workers in one process. In production you'd
 * split workers across pods; here single-process keeps the dev story
 * simple and lets us still observe per-queue concurrency profiles.
 */
import "./parse-file.worker.js";
import "./parse-sheet.worker.js";
import "./upload-image.worker.js";
import "./stream-inventory.worker.js";
import { logger } from "../../lib/logger.js";
import { closeQueues } from "../queues.js";
import { closeDb } from "../../storage/db/client.js";
import { closeR2Client } from "../../storage/r2-uploader.js";

logger.info("All workers started. Press Ctrl+C to stop.");

async function shutdown(signal: string): Promise<void> {
  logger.info({ signal }, "Shutting down workers...");
  try {
    await closeQueues();
    await closeR2Client();
    await closeDb();
  } catch (err) {
    logger.error({ err }, "Shutdown error");
  }
  process.exit(0);
}

process.on("SIGINT", () => void shutdown("SIGINT"));
process.on("SIGTERM", () => void shutdown("SIGTERM"));
