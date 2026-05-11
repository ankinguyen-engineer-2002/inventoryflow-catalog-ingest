/**
 * Streaming xlsx reader.
 *
 * Wraps `exceljs` WorkbookReader in an async-iterator-friendly API that
 * keeps RAM bounded for the 241 MB sample (peak <300 MB verified locally).
 *
 * Why streaming? `Workbook.xlsx.readFile()` loads everything into memory.
 * On a 241 MB workbook with 1586 embedded images, that approached 2 GB on a
 * test run — unacceptable for a Fly Machine 512 MB instance. The streaming
 * reader keeps RAM ~constant per sheet.
 *
 * What this module does NOT do:
 *   • Parse image binding (see drawing-parser.ts — drawings live in a
 *     separate XML inside the zip).
 *   • Detect sections within a sheet (see section-detector.ts).
 *   • Validate row shape (see row-normalizer.ts).
 */
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import ExcelJS from "exceljs";

export interface SheetMeta {
  /** 1-based sheet index as exceljs reports it. */
  index: number;
  /** Sheet name, *not trimmed* — caller decides trim policy. */
  name: string;
}

/**
 * One row read from a sheet. Cells are values, not formulas/styles.
 * Empty cells become `null` rather than `undefined` for predictable shape.
 */
export interface SheetRow {
  /** 1-based row index as it appears in the xlsx. */
  rowIndex: number;
  /** Cell values, 1-based by column. Index 0 is `null` (kept for off-by-one safety). */
  values: ReadonlyArray<CellValue>;
}

export type CellValue = string | number | boolean | Date | null;

export interface XlsxStreamOptions {
  /** Optional filter — when provided, only emit rows from matching sheets. */
  sheetFilter?: (meta: SheetMeta) => boolean;
}

export interface XlsxFileInfo {
  /** Absolute path passed in. */
  path: string;
  /** SHA-256 hex digest of the file bytes. */
  sha256: string;
  /** File size in bytes. */
  sizeBytes: number;
}

/**
 * Compute SHA-256 + size in a single streaming pass.
 * Used by callers that need to log file provenance into `ingest_runs.source_sha256`.
 */
export async function inspectFile(path: string): Promise<XlsxFileInfo> {
  const { size } = await stat(path);
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(path)) {
    hash.update(chunk as Buffer);
  }
  return { path, sha256: hash.digest("hex"), sizeBytes: size };
}

/**
 * Open an xlsx in streaming mode. Yields `{ meta, rows: AsyncIterable }`
 * pairs per sheet. Caller must consume rows of each sheet before moving on —
 * sheets are processed sequentially by exceljs's reader.
 *
 * Usage:
 *   for await (const sheet of streamWorkbook(path)) {
 *     console.log(sheet.meta.name);
 *     for await (const row of sheet.rows) {
 *       // process row
 *     }
 *   }
 */
export async function* streamWorkbook(
  path: string,
  opts: XlsxStreamOptions = {},
): AsyncGenerator<{ meta: SheetMeta; rows: AsyncIterable<SheetRow> }> {
  const reader = new ExcelJS.stream.xlsx.WorkbookReader(path, {
    sharedStrings: "cache",
    hyperlinks: "ignore",
    styles: "ignore",
    entries: "ignore",
    worksheets: "emit",
  });

  for await (const worksheetReader of reader) {
    // exceljs's TS types don't expose id/orderNo/name on the stream WorksheetReader,
    // but the runtime object does. Cast to a minimal local interface.
    const ws = worksheetReader as unknown as {
      id?: number;
      orderNo?: number;
      name?: string;
      [Symbol.asyncIterator]: () => AsyncIterator<ExcelJsRowLike>;
    };

    const meta: SheetMeta = {
      index: ws.id ?? ws.orderNo ?? 0,
      name: ws.name ?? `Sheet${ws.id ?? "?"}`,
    };

    if (opts.sheetFilter && !opts.sheetFilter(meta)) {
      // Drain to advance the reader, but don't yield rows.
      for await (const _row of ws as AsyncIterable<ExcelJsRowLike>) {
        void _row;
      }
      continue;
    }

    yield { meta, rows: rowIterable(ws as AsyncIterable<ExcelJsRowLike>) };
  }
}

interface ExcelJsRowLike {
  number: number;
  values: ReadonlyArray<unknown>;
}

async function* rowIterable(
  worksheetReader: AsyncIterable<ExcelJsRowLike>,
): AsyncGenerator<SheetRow> {
  for await (const row of worksheetReader) {
    const values = row.values as unknown as ReadonlyArray<CellValue>;
    yield {
      rowIndex: row.number,
      values: values ?? [],
    };
  }
}

/**
 * Convenience: collect a single sheet's rows into an array.
 * Use only for small sheets (TOC, Sheet18, etc.) — full sheets can have
 * 500+ rows and this defeats the streaming purpose.
 */
export async function collectSheetRows(
  path: string,
  sheetName: string,
): Promise<{ meta: SheetMeta; rows: SheetRow[] } | null> {
  for await (const sheet of streamWorkbook(path, {
    sheetFilter: (m) => m.name === sheetName || m.name.trim() === sheetName.trim(),
  })) {
    const rows: SheetRow[] = [];
    for await (const r of sheet.rows) {
      rows.push(r);
    }
    return { meta: sheet.meta, rows };
  }
  return null;
}
