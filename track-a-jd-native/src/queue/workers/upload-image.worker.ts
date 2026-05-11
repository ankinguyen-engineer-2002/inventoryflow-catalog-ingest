/**
 * upload-image worker.
 *
 * Loads bytes from the xlsx zip (cached per-process), uploads to R2 with
 * SHA-256-keyed dedup, links products → images in the DB.
 *
 * Concurrency: 16. Rate limiter: 800 req/sec (under R2's 1k cap).
 */
import { Worker } from "bullmq";
import { QUEUE_NAMES, workerOptions, type UploadImageJob } from "../queues.js";
import { openImageArchive, getImageBytes } from "../../storage/image-extractor.js";
import { uploadImage } from "../../storage/r2-uploader.js";
import { linkImage } from "../../storage/db/repositories/images.repo.js";
import { logger, withRun } from "../../lib/logger.js";

/** Per-process zip-directory cache keyed by file path. */
const archives = new Map<string, Awaited<ReturnType<typeof openImageArchive>>>();

export const uploadImageWorker = new Worker<UploadImageJob>(
  QUEUE_NAMES.uploadImage,
  async (job) => {
    const { runId, filePath, imageFile, productIds, sectionLabel, sourceSheet } = job.data;
    const log = withRun(runId).child({ worker: "upload-image", imageFile, jobId: job.id });

    // Resolve archive (cached).
    let archive = archives.get(filePath);
    if (!archive) {
      archive = await openImageArchive(filePath);
      archives.set(filePath, archive);
    }

    const bytes = await getImageBytes(archive, imageFile);
    if (!bytes) {
      log.warn("image file not found in archive; skipping");
      return { skipped: true };
    }

    const ext = (imageFile.split(".").pop() ?? "bin").toLowerCase();
    const result = await uploadImage(bytes, ext);

    // Link every product that referenced this image.
    for (const productId of productIds) {
      await linkImage({
        productId,
        r2Key: result.key,
        r2Url: result.url,
        sha256: result.sha256,
        sectionLabel: sectionLabel ?? null,
        sourceSheet: sourceSheet ?? null,
      });
    }

    log.debug(
      { sha256: result.sha256.slice(0, 12), uploaded: result.uploaded, products: productIds.length },
      "image processed",
    );
    return { sha256: result.sha256, uploaded: result.uploaded, linkedProducts: productIds.length };
  },
  workerOptions(QUEUE_NAMES.uploadImage),
);

uploadImageWorker.on("failed", (job, err) => {
  logger.error({ jobId: job?.id, err }, "upload-image job failed");
});
