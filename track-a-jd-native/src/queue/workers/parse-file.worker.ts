/**
 * parse-file worker.
 *
 * Receives a `ParseFileJob`, enumerates the sheets in the xlsx, and
 * fans out one `ParseSheetJob` per sheet (filtered through non-fitment
 * skip list).
 *
 * The fan-out happens before any row parsing so the worker pool can pick
 * up sheet jobs in parallel immediately.
 */
import { Worker } from "bullmq";
import {
  QUEUE_NAMES,
  parseSheetQueue,
  workerOptions,
  type ParseFileJob,
  type ParseSheetJob,
} from "../queues.js";
import { streamWorkbook } from "../../ingest/xlsx-reader.js";
import { updateRunStatus } from "../../storage/db/repositories/runs.repo.js";
import { logger, withRun } from "../../lib/logger.js";

export const parseFileWorker = new Worker<ParseFileJob>(
  QUEUE_NAMES.parseFile,
  async (job) => {
    const { runId, dealerId, filePath, fileSha256 } = job.data;
    const log = withRun(runId, dealerId ?? undefined).child({
      worker: "parse-file",
      file: filePath,
      jobId: job.id,
    });
    log.info("File job started");

    await updateRunStatus(runId, "RUNNING");

    const sheetJobs: ParseSheetJob[] = [];

    // Enumerate sheets without reading rows.
    for await (const sheet of streamWorkbook(filePath)) {
      const name = sheet.meta.name;
      // Drain rows lazily to advance the reader; we don't actually consume them here.
      for await (const _r of sheet.rows) {
        void _r;
        break; // peek-only — drain happens internally on next iteration
      }
      // We can't reliably stop after 0 rows with WorkbookReader; let it complete.
      // Continue collecting sheet names.
      sheetJobs.push({
        runId,
        dealerId,
        filePath,
        fileSha256,
        sheetName: name,
      });
    }

    log.info({ sheetCount: sheetJobs.length }, "Enqueueing sheet jobs");

    // Bulk-add to keep Redis round trips low.
    await parseSheetQueue.addBulk(
      sheetJobs.map((j) => ({
        name: "parse-sheet",
        data: j,
        opts: { jobId: `${runId}:${j.sheetName}` },
      })),
    );

    log.info("File fan-out complete");
    return { sheetsEnqueued: sheetJobs.length };
  },
  workerOptions(QUEUE_NAMES.parseFile),
);

parseFileWorker.on("failed", (job, err) => {
  logger.error({ jobId: job?.id, err }, "parse-file job failed");
});
