#!/usr/bin/env node
/**
 * Ingest CLI.
 *
 * Modes:
 *   --dry-run    Parse only; emit JSON section summary to stdout.
 *   (default)    Full ingest: enqueue + run synchronously, persist to DB + R2.
 *
 * Examples:
 *   pnpm ingest:dryrun ../shared/sample-data/example.xlsx --sheet "AY70-2" --limit 5
 *   pnpm ingest ../shared/sample-data/example.xlsx
 */
import { parseArgs } from "node:util";
import { resolve } from "node:path";

import { streamWorkbook, inspectFile, type SheetRow } from "../ingest/xlsx-reader.js";
import { parseAllDrawings } from "../ingest/drawing-parser.js";
import { detectSections, type DetectedSection } from "../ingest/section-detector.js";
import { resolveFitmentFromSheetName } from "../ingest/fitment-resolver.js";
import { normaliseRow } from "../ingest/row-normalizer.js";
import { upsertProduct } from "../storage/db/repositories/products.repo.js";
import { linkImage } from "../storage/db/repositories/images.repo.js";
import { createRun, finaliseRun } from "../storage/db/repositories/runs.repo.js";
import { openImageArchive, getImageBytes } from "../storage/image-extractor.js";
import { uploadImage } from "../storage/r2-uploader.js";
import { logger, withRun } from "../lib/logger.js";
import { closeDb } from "../storage/db/client.js";
import { closeR2Client } from "../storage/r2-uploader.js";
import type { FitmentEntry } from "../storage/db/schema.js";

interface CliOptions {
  file: string;
  dryRun: boolean;
  sheet?: string;
  limit?: number;
  dealerId?: string;
}

function parseCliArgs(): CliOptions {
  const { values, positionals } = parseArgs({
    options: {
      "dry-run": { type: "boolean", default: false },
      sheet: { type: "string" },
      limit: { type: "string" },
      "dealer-id": { type: "string" },
      help: { type: "boolean", default: false },
    },
    allowPositionals: true,
  });

  if (values.help || positionals.length === 0) {
    printHelp();
    process.exit(values.help ? 0 : 1);
  }

  return {
    file: resolve(positionals[0]!),
    dryRun: Boolean(values["dry-run"]),
    ...(values.sheet ? { sheet: values.sheet } : {}),
    ...(values.limit ? { limit: Number(values.limit) } : {}),
    ...(values["dealer-id"] ? { dealerId: values["dealer-id"] } : {}),
  };
}

function printHelp(): void {
  // eslint-disable-next-line no-console
  console.log(`
Usage:
  pnpm ingest <xlsx-path> [options]

Options:
  --dry-run         Parse only; no DB/R2 writes. Print JSON summary.
  --sheet <name>    Only process sheets matching this substring.
  --limit <N>       Stop after processing N sections.
  --dealer-id <id>  Tag products with this dealer_id (UUID).
  --help            Show this help.
`);
}

async function main(): Promise<number> {
  const opts = parseCliArgs();
  const log = logger.child({ cli: "ingest", file: opts.file, dryRun: opts.dryRun });

  log.info("Inspecting file...");
  const info = await inspectFile(opts.file);
  log.info(
    { sizeMB: (info.sizeBytes / 1024 / 1024).toFixed(1), sha256: info.sha256.slice(0, 12) },
    "File inspected",
  );

  log.info("Parsing drawings (image anchors)...");
  const drawings = await parseAllDrawings(opts.file);
  log.info({ sheetsWithImages: drawings.size }, "Drawings parsed");

  // For dry-run, just emit the summary and exit.
  if (opts.dryRun) {
    return await runDryRun(opts, info);
  }

  // Full ingest path.
  const run = await createRun({
    dealerId: opts.dealerId ?? null,
    sourceFile: opts.file,
    sourceSha256: info.sha256,
    status: "RUNNING",
  });
  const rlog = withRun(run.runId, opts.dealerId ?? undefined).child({ cli: "ingest" });
  rlog.info({ sizeMB: (info.sizeBytes / 1024 / 1024).toFixed(1) }, "Run started");

  const counters = { rowsAttempted: 0, rowsSucceeded: 0, rowsFailed: 0, imagesUploaded: 0 };
  const archive = await openImageArchive(opts.file);

  const sheetFilter = opts.sheet
    ? (m: { name: string }) => m.name.trim().includes(opts.sheet!.trim())
    : undefined;

  let sectionsProcessed = 0;
  for await (const sheet of streamWorkbook(opts.file, sheetFilter ? { sheetFilter } : {})) {
    const rows: SheetRow[] = [];
    for await (const r of sheet.rows) rows.push(r);

    const sections = detectSections(rows);
    const fitmentInfo = resolveFitmentFromSheetName(sheet.meta.name);

    // Collect all anchors (we don't have sheet→drawing mapping reliably).
    const allAnchors = Array.from(drawings.values()).flat();

    for (const section of sections) {
      if (opts.limit && sectionsProcessed >= opts.limit) break;
      sectionsProcessed++;

      const anchor = pickAnchorForSection(allAnchors, section);
      let imageKeyForSection: string | null = null;

      // Upload section image once (idempotent — SHA-256-keyed).
      if (anchor) {
        try {
          const bytes = await getImageBytes(archive, anchor.imageFile);
          if (bytes) {
            const ext = (anchor.imageFile.split(".").pop() ?? "bin").toLowerCase();
            const upl = await uploadImage(bytes, ext);
            imageKeyForSection = upl.key;
            if (upl.uploaded) counters.imagesUploaded++;
          }
        } catch (err) {
          rlog.warn({ err, imageFile: anchor.imageFile }, "image upload failed");
        }
      }

      for (const r of rows) {
        if (r.rowIndex < section.dataStartIndex || r.rowIndex > section.dataEndIndex) continue;
        counters.rowsAttempted++;
        const norm = normaliseRow(r, section, sheet.meta.name);
        if (!norm.ok) {
          counters.rowsFailed++;
          continue;
        }

        const fitment: FitmentEntry[] = fitmentInfo
          ? [buildFitmentEntry(fitmentInfo, section, norm.row.callout?.raw ?? null)]
          : [];

        try {
          const { productId } = await upsertProduct({
            row: norm.row,
            fitment,
            sourceFileSha256: info.sha256,
            sourceDealerId: opts.dealerId ?? null,
            primaryImageR2Key: imageKeyForSection,
          });
          counters.rowsSucceeded++;

          if (imageKeyForSection && anchor) {
            await linkImage({
              productId,
              r2Key: imageKeyForSection,
              r2Url: r2Url(imageKeyForSection),
              sha256: imageKeyForSection.split("/").pop()?.split(".")[0] ?? "",
              sectionLabel: section.title ?? null,
              sourceSheet: sheet.meta.name,
            });
          }
        } catch (err) {
          counters.rowsFailed++;
          rlog.error({ err, row: r.rowIndex }, "row upsert failed");
        }
      }
    }

    rlog.info(
      { sheet: sheet.meta.name, rows: rows.length, sectionsProcessed, ...counters },
      "Sheet complete",
    );

    if (opts.limit && sectionsProcessed >= opts.limit) break;
  }

  await finaliseRun(run.runId, {
    status: counters.rowsFailed === 0 ? "SUCCESS" : "PARTIAL",
    rowsAttempted: counters.rowsAttempted,
    rowsSucceeded: counters.rowsSucceeded,
    rowsFailed: counters.rowsFailed,
  });

  rlog.info({ runId: run.runId, ...counters }, "Run complete");
  return counters.rowsFailed === 0 ? 0 : 2;
}

async function runDryRun(opts: CliOptions, info: { sha256: string; sizeBytes: number }): Promise<number> {
  let sheetsSeen = 0;
  let sectionsTotal = 0;
  const summary: Array<Record<string, unknown>> = [];

  const sheetFilter = opts.sheet
    ? (m: { name: string }) => m.name.trim().includes(opts.sheet!.trim())
    : undefined;

  for await (const sheet of streamWorkbook(opts.file, sheetFilter ? { sheetFilter } : {})) {
    sheetsSeen++;
    const rows: SheetRow[] = [];
    for await (const r of sheet.rows) rows.push(r);
    const sections = detectSections(rows);
    const fitment = resolveFitmentFromSheetName(sheet.meta.name);
    sectionsTotal += sections.length;
    summary.push({
      sheet: sheet.meta.name,
      rowsRead: rows.length,
      sectionsDetected: sections.length,
      fitment,
      sections: sections.slice(0, 3).map((s) => ({
        kind: s.kind,
        headerRow: s.headerRowIndex,
        dataRows: s.dataEndIndex - s.dataStartIndex + 1,
        title: s.title ?? null,
      })),
    });
    if (opts.limit && sectionsTotal >= opts.limit) break;
  }

  // eslint-disable-next-line no-console
  console.log(
    JSON.stringify(
      {
        file: opts.file,
        sha256: info.sha256,
        sizeMB: (info.sizeBytes / 1024 / 1024).toFixed(1),
        sheetsProcessed: sheetsSeen,
        sectionsTotal,
        summary,
      },
      null,
      2,
    ),
  );
  return 0;
}

function buildFitmentEntry(
  info: ReturnType<typeof resolveFitmentFromSheetName>,
  section: DetectedSection,
  callout: string | null,
): FitmentEntry {
  return {
    year: info?.yearStart ?? 0,
    make: "Kayo",
    model: info?.modelCode ?? "UNKNOWN",
    model_code: info?.modelCode ?? null,
    variant: info?.variant ?? null,
    section: section.title ?? null,
    callout_no: callout,
    confidence: info?.modelCode ? "high" : "low",
  };
}

function pickAnchorForSection(
  anchors: Array<{ row: number; imageFile: string }>,
  section: DetectedSection,
): { row: number; imageFile: string } | null {
  const headerRow0 = section.headerRowIndex - 1;
  let best: { row: number; imageFile: string } | null = null;
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

function r2Url(key: string): string {
  // Mirror logic from r2-uploader.keyToUrl without importing private fn.
  // Kept here only for the CLI direct path; workers use the upload result.
  return `${process.env["S3_ENDPOINT"] ?? "http://localhost:9000"}/${process.env["S3_BUCKET"] ?? "catalog"}/${key}`;
}

main()
  .then(async (code) => {
    await closeR2Client();
    await closeDb();
    process.exit(code);
  })
  .catch(async (err: unknown) => {
    logger.error({ err }, "CLI failed");
    await closeR2Client().catch(() => {});
    await closeDb().catch(() => {});
    process.exit(1);
  });
