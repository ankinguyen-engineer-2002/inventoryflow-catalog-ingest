/**
 * parse-sheet worker.
 *
 * Receives `ParseSheetJob` (one sheet at a time), streams its rows,
 * detects sections, normalises rows, upserts products, and enqueues
 * upload-image jobs for any schematic image bound to a section.
 *
 * Why not do all of this in parse-file? Per-sheet isolation:
 *   • Failures in one sheet don't block others.
 *   • Worker pool can parallelise across sheets (conc=8 by default).
 *   • Retry/backoff applies per-sheet, not per-file.
 */
import { Worker } from "bullmq";
import { QUEUE_NAMES, uploadImageQueue, workerOptions, type ParseSheetJob, type UploadImageJob } from "../queues.js";
import { streamWorkbook, type SheetRow } from "../../ingest/xlsx-reader.js";
import { parseAllDrawings, type DrawingAnchor } from "../../ingest/drawing-parser.js";
import { detectSections, type DetectedSection } from "../../ingest/section-detector.js";
import { resolveFitmentFromSheetName } from "../../ingest/fitment-resolver.js";
import { normaliseRow } from "../../ingest/row-normalizer.js";
import { upsertProduct } from "../../storage/db/repositories/products.repo.js";
import { logger, withRun } from "../../lib/logger.js";
import type { FitmentEntry } from "../../storage/db/schema.js";

interface SheetCounters {
  rowsAttempted: number;
  rowsSucceeded: number;
  rowsFailed: number;
  sectionsDetected: number;
}

/** Per-process cache of drawings parsed per file path. */
const drawingsCache = new Map<string, Map<string, DrawingAnchor[]>>();

export const parseSheetWorker = new Worker<ParseSheetJob>(
  QUEUE_NAMES.parseSheet,
  async (job) => {
    const { runId, dealerId, filePath, sheetName, rowLimit } = job.data;
    const log = withRun(runId, dealerId ?? undefined).child({
      worker: "parse-sheet",
      sheet: sheetName,
      jobId: job.id,
    });
    log.info("Sheet job started");

    // Lazy-load drawings index per file.
    let drawings = drawingsCache.get(filePath);
    if (!drawings) {
      drawings = await parseAllDrawings(filePath);
      drawingsCache.set(filePath, drawings);
    }

    const counters: SheetCounters = {
      rowsAttempted: 0,
      rowsSucceeded: 0,
      rowsFailed: 0,
      sectionsDetected: 0,
    };

    // Stream this single sheet.
    const sheetIter = streamWorkbook(filePath, {
      sheetFilter: (m) => m.name === sheetName || m.name.trim() === sheetName.trim(),
    });

    for await (const sheet of sheetIter) {
      const rows: SheetRow[] = [];
      for await (const r of sheet.rows) {
        rows.push(r);
        if (rowLimit && rows.length >= rowLimit) break;
      }

      const sections = detectSections(rows);
      counters.sectionsDetected = sections.length;

      const fitmentInfo = resolveFitmentFromSheetName(sheet.meta.name);
      // The sheet path within the zip — we don't know it directly from
      // exceljs, but the drawings index is keyed by sheet xml path. We
      // match by sheet name → drawing via shared anchor enumeration.
      const sheetAnchors = pickAnchorsForSheet(drawings, sheet.meta.index);

      for (const section of sections) {
        // Persist this section's data rows.
        for (const r of rows) {
          if (r.rowIndex < section.dataStartIndex || r.rowIndex > section.dataEndIndex) {
            continue;
          }

          counters.rowsAttempted++;
          const norm = normaliseRow(r, section, sheet.meta.name);
          if (!norm.ok) {
            counters.rowsFailed++;
            log.debug({ row: r.rowIndex, reason: norm.reason }, "row skipped");
            continue;
          }

          const fitment: FitmentEntry[] = fitmentInfo
            ? [buildFitmentEntry(fitmentInfo, section, norm.row.callout?.raw ?? null, dealerId)]
            : [];

          try {
            const { productId } = await upsertProduct({
              row: norm.row,
              fitment,
              sourceFileSha256: job.data.fileSha256,
              sourceDealerId: dealerId,
            });
            counters.rowsSucceeded++;

            // Enqueue image upload for this product if there's a section image.
            const anchor = pickAnchorForSection(sheetAnchors, section);
            if (anchor) {
              const imgJob: UploadImageJob = {
                runId,
                filePath,
                imageFile: anchor.imageFile,
                productIds: [productId],
                ...(section.title ? { sectionLabel: section.title } : {}),
                sourceSheet: sheet.meta.name,
              };
              await uploadImageQueue.add("upload-image", imgJob, {
                jobId: `${runId}:${anchor.imageFile}:${productId}`,
              });
            }
          } catch (err) {
            counters.rowsFailed++;
            log.error({ err, row: r.rowIndex }, "row upsert failed");
          }
        }
      }

      log.info(counters, "Sheet job complete");
      return counters;
    }

    // sheet not found
    log.warn("Sheet not found in workbook (filter returned nothing)");
    return counters;
  },
  workerOptions(QUEUE_NAMES.parseSheet),
);

parseSheetWorker.on("failed", (job, err) => {
  logger.error({ jobId: job?.id, err }, "parse-sheet job failed");
});

function buildFitmentEntry(
  info: ReturnType<typeof resolveFitmentFromSheetName>,
  section: DetectedSection,
  callout: string | null,
  dealerId: string | null,
): FitmentEntry {
  // Make is sourced from per-dealer config; defaults to "Kayo" until the
  // dispatch-loop wires dealers.inferred_make into the job payload.
  // See src/cli/dispatch-loop.ts and ADR-014.
  const make = "Kayo";
  // Open-ended years are represented as a single null year_end on a single entry.
  return {
    year: info?.yearStart ?? 0,
    make,
    model: info?.modelCode ?? "UNKNOWN",
    model_code: info?.modelCode ?? null,
    variant: info?.variant ?? null,
    section: section.title ?? null,
    callout_no: callout,
    confidence: info?.modelCode ? "high" : "low",
    // dealer_id intentionally NOT in fitment (provenance lives on products row)
  };
  void dealerId;
}

function pickAnchorsForSheet(
  drawings: Map<string, DrawingAnchor[]>,
  sheetIdx: number,
): DrawingAnchor[] {
  // Drawings are keyed by "xl/worksheets/sheetN.xml". We don't know if
  // exceljs's sheet `id` matches the N in the filename; many xlsx files
  // don't. So we just return ALL anchors and let pickAnchorForSection
  // match by row proximity within the section's rows.
  void sheetIdx;
  const all: DrawingAnchor[] = [];
  for (const v of drawings.values()) all.push(...v);
  return all;
}

function pickAnchorForSection(
  anchors: DrawingAnchor[],
  section: DetectedSection,
): DrawingAnchor | null {
  // Find the anchor whose `row` is closest to (and BEFORE) the section header.
  // anchors are 0-indexed; section.headerRowIndex is 1-indexed.
  const headerRow0 = section.headerRowIndex - 1;
  let best: DrawingAnchor | null = null;
  let bestDist = Infinity;
  for (const a of anchors) {
    const dist = headerRow0 - a.row;
    if (dist >= 0 && dist < bestDist) {
      best = a;
      bestDist = dist;
    }
  }
  return best;
}
