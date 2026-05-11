#!/usr/bin/env node
/**
 * CLI entrypoint for ingesting an xlsx.
 *
 * Day 1 PM scope: --dry-run only. Parses the file, prints per-sheet section
 * detection results to stdout as JSON. No DB writes, no R2 uploads.
 *
 * Day 2 will extend this to:
 *   • full ingest (DB upserts, R2 uploads)
 *   • run registry tracking
 *   • LLM enrichment of missing name_en fields
 */
import { parseArgs } from "node:util";
import { resolve } from "node:path";
import { streamWorkbook, inspectFile, type SheetRow } from "../ingest/xlsx-reader.js";
import { parseAllDrawings } from "../ingest/drawing-parser.js";
import { detectSections, type DetectedSection } from "../ingest/section-detector.js";
import { resolveFitmentFromSheetName } from "../ingest/fitment-resolver.js";
import { logger } from "../lib/logger.js";

interface CliOptions {
  file: string;
  dryRun: boolean;
  sheet?: string;
  limit?: number;
}

function parseCliArgs(): CliOptions {
  const { values, positionals } = parseArgs({
    options: {
      "dry-run": { type: "boolean", default: false },
      sheet: { type: "string" },
      limit: { type: "string" },
      help: { type: "boolean", default: false },
    },
    allowPositionals: true,
  });

  if (values.help || positionals.length === 0) {
    printHelp();
    process.exit(values.help ? 0 : 1);
  }

  const file = resolve(positionals[0]!);
  return {
    file,
    dryRun: Boolean(values["dry-run"]),
    ...(values.sheet ? { sheet: values.sheet } : {}),
    ...(values.limit ? { limit: Number(values.limit) } : {}),
  };
}

function printHelp(): void {
  // eslint-disable-next-line no-console
  console.log(`
Usage:
  pnpm ingest <xlsx-path> [options]

Options:
  --dry-run         Parse only; no DB writes, no R2 uploads. Print JSON summary.
  --sheet <name>    Only process the named sheet (substring match after trim).
  --limit <N>       Stop after processing N sections (across all sheets).
  --help            Show this help.

Examples:
  pnpm ingest:dryrun ../shared/sample-data/example.xlsx --sheet "FOXStorm 70 AY70-2"
  pnpm ingest:dryrun ../shared/sample-data/example.xlsx --limit 5
`);
}

async function main(): Promise<void> {
  const opts = parseCliArgs();
  const log = logger.child({ file: opts.file, dryRun: opts.dryRun });

  log.info("Inspecting file...");
  const info = await inspectFile(opts.file);
  log.info(
    { sizeMB: (info.sizeBytes / 1024 / 1024).toFixed(1), sha256: info.sha256.slice(0, 12) },
    "File inspected",
  );

  log.info("Parsing drawings (image anchors)...");
  const drawings = await parseAllDrawings(opts.file);
  log.info({ sheetsWithImages: drawings.size }, "Drawings parsed");

  let sheetsSeen = 0;
  let sectionsTotal = 0;
  const summary: Array<Record<string, unknown>> = [];

  const sheetFilter = opts.sheet
    ? (m: { name: string }) => m.name.trim().includes(opts.sheet!.trim())
    : undefined;

  for await (const sheet of streamWorkbook(opts.file, sheetFilter ? { sheetFilter } : {})) {
    sheetsSeen++;
    const rows: SheetRow[] = [];
    for await (const r of sheet.rows) {
      rows.push(r);
    }

    const sections = detectSections(rows);
    const fitment = resolveFitmentFromSheetName(sheet.meta.name);

    sectionsTotal += sections.length;

    summary.push({
      sheet: sheet.meta.name,
      rowsRead: rows.length,
      sectionsDetected: sections.length,
      fitment,
      sections: sections.slice(0, 3).map(summariseSection),
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

  log.info(
    { sheetsProcessed: sheetsSeen, sectionsTotal },
    opts.dryRun ? "Dry run complete" : "Ingest complete",
  );
}

function summariseSection(s: DetectedSection): Record<string, unknown> {
  return {
    kind: s.kind,
    headerRow: s.headerRowIndex,
    dataRows: s.dataEndIndex - s.dataStartIndex + 1,
    title: s.title ?? null,
    columns: Array.from(s.columnMap.keys()),
  };
}

main().catch((err: unknown) => {
  logger.error({ err }, "CLI failed");
  process.exit(1);
});
