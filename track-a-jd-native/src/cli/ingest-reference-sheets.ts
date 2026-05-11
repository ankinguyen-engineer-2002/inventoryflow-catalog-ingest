#!/usr/bin/env node
/**
 * Ingest the ~12 "exception" sheets into reference_specs.
 *
 * Main `ingest` skips these because they don't match the three parts-catalog
 * header signatures. They're reference/compatibility tables with completely
 * different schemas — spark plugs by model, carburetor jet sizes by
 * displacement, wheel bolt patterns, fork seal specs, etc.
 *
 * Rather than build a separate parser per sheet, we treat them as
 * semi-structured: skip header rows, treat each non-empty row as a record,
 * and store the entire row as JSONB in `reference_specs.attributes`. The
 * category column (derived from sheet name) is the access key.
 *
 * Usage:  pnpm ingest:references ../shared/sample-data/example.xlsx
 */
import { resolve } from "node:path";
import { streamWorkbook, type SheetRow } from "../ingest/xlsx-reader.js";
import { cellToString } from "../ingest/cell-utils.js";
import { sql, closeDb } from "../storage/db/client.js";
import { logger } from "../lib/logger.js";

interface ReferenceSheetSpec {
  /** Sheet name match (case-insensitive substring after trim). */
  match: string;
  /** Category key stored in reference_specs.category. */
  category: string;
  /** How many header rows to skip from the top. */
  headerRows: number;
  /** Optional: column index (1-based) that holds the model code. */
  modelCodeCol?: number;
}

const SHEETS: ReadonlyArray<ReferenceSheetSpec> = [
  { match: "SPARK PLUGS",      category: "spark_plugs",       headerRows: 1, modelCodeCol: 1 },
  { match: "ATV Wheel specs",  category: "atv_wheel_specs",   headerRows: 1, modelCodeCol: 1 },
  { match: "Spoke Specs",      category: "spoke_specs",       headerRows: 1, modelCodeCol: 1 },
  { match: "Battery specs",    category: "battery_specs",     headerRows: 1, modelCodeCol: 1 },
  { match: "Fork seal specs",  category: "fork_seal_specs",   headerRows: 1, modelCodeCol: 1 },
  { match: "Carburetor Jets",  category: "carburetor_jets",   headerRows: 2 },
  { match: "DirtbikePitbike",  category: "wheel_bearings",    headerRows: 1, modelCodeCol: 1 },
  { match: "SNOW TRACK",       category: "snow_track_kit",    headerRows: 1 },
  { match: "ski kit parts",    category: "ski_kit",           headerRows: 1 },
  { match: "Owners manuals",   category: "owners_manuals",    headerRows: 1, modelCodeCol: 1 },
  { match: "eA110 upgrade kit", category: "upgrade_kit",      headerRows: 1 },
];

async function main(): Promise<void> {
  const file = process.argv[2];
  if (!file) {
    // eslint-disable-next-line no-console
    console.error("Usage: pnpm ingest:references <xlsx-path>");
    process.exit(1);
  }
  const xlsxPath = resolve(file);
  const log = logger.child({ cli: "ingest-reference-sheets", file: xlsxPath });

  const counters: Record<string, number> = {};

  for await (const sheet of streamWorkbook(xlsxPath, {
    sheetFilter: (m) => SHEETS.some((s) => m.name.trim().toUpperCase().includes(s.match.toUpperCase())),
  })) {
    const spec = SHEETS.find((s) =>
      sheet.meta.name.trim().toUpperCase().includes(s.match.toUpperCase()),
    );
    if (!spec) continue;

    const rows: SheetRow[] = [];
    for await (const r of sheet.rows) rows.push(r);

    // Capture header labels from the first headerRow we see.
    const headerLabels = rows[spec.headerRows - 1]?.values.slice(1).map((v) => cellToString(v) ?? "") ?? [];
    const dataRows = rows.slice(spec.headerRows);

    let inserted = 0;
    for (const r of dataRows) {
      const cells = r.values.slice(1); // drop the off-by-one safety null
      const nonEmpty = cells.some((c) => cellToString(c) !== null);
      if (!nonEmpty) continue;

      const attributes: Record<string, unknown> = {};
      cells.forEach((cell, i) => {
        const label = headerLabels[i]?.trim() || `col_${i + 1}`;
        const value = cellToString(cell);
        if (value !== null) attributes[label] = value;
      });
      if (Object.keys(attributes).length === 0) continue;

      const modelCode = spec.modelCodeCol ? cellToString(cells[spec.modelCodeCol - 1] ?? null) : null;

      await sql`
        INSERT INTO reference_specs (category, model_code, attributes, source_sheet, source_row)
        VALUES (${spec.category}, ${modelCode}, ${JSON.stringify(attributes)}::jsonb,
                ${sheet.meta.name.trim()}, ${r.rowIndex})
      `;
      inserted++;
    }

    counters[spec.category] = (counters[spec.category] ?? 0) + inserted;
    log.info({ sheet: sheet.meta.name.trim(), category: spec.category, inserted }, "sheet ingested");
  }

  log.info({ counters, total: Object.values(counters).reduce((a, b) => a + b, 0) }, "Reference sheets ingested");
}

main()
  .then(async () => {
    await closeDb();
    process.exit(0);
  })
  .catch(async (err: unknown) => {
    logger.error({ err }, "ingest-references failed");
    await closeDb().catch(() => {});
    process.exit(1);
  });
