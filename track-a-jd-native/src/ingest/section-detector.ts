/**
 * Section detector.
 *
 * Each parts sheet in the xlsx is multi-section: 10–20 sections per sheet,
 * each consisting of (image, title row, header row, data rows, blank rows).
 * The header row REPEATS within a single sheet — this is the key signal.
 *
 * We don't iterate from a fixed row index. We scan all rows, recognise any
 * row whose trimmed cell values match a known header signature, and treat
 * that row as a section boundary.
 *
 * See ADR-005 for the design reasoning.
 */
import type { CellValue, SheetRow } from "./xlsx-reader.js";
import { cellToString } from "./cell-utils.js";

/** A header signature is the set of column labels we expect, in order. */
export interface HeaderSignature {
  readonly kind: "chassis" | "engine" | "chassis_u8" | "reference";
  readonly columns: ReadonlyArray<string>;
  /**
   * Required columns — a header row must contain all of these (in any cell).
   * Less strict than full order match, accommodating slight column reordering.
   */
  readonly required: ReadonlyArray<string>;
  /** Which column holds the canonical part number for this schema. */
  readonly partNumberColumn: string;
}

/* The two canonical signatures observed in the Kayo sample.
 * New dealer schemas can be appended via `rules.yaml` and merged in here. */
export const SIGNATURES: ReadonlyArray<HeaderSignature> = [
  // Variant 1: classic chassis sheets ("FOXStorm 70 AY70-2 ", etc.)
  {
    kind: "chassis",
    columns: [
      "No.",
      "Part Number",
      "EN name",
      "CN name",
      "Specifications in CN",
      "Qty/vehicle",
      "Dealer",
      "QTY",
      "Retail",
    ],
    required: ["No.", "Part Number", "EN name", "CN name"],
    partNumberColumn: "Part Number",
  },
  // Variant 2: engine sheets ("FOXStorm 70 AY70-2 Engine ", etc.)
  {
    kind: "engine",
    columns: [
      "No.",
      "OLD PART NUMBER",
      "NEW PART NUMBER",
      "EN name",
      "CN name",
      "Qty/vehicle",
      "Dealer",
      "QTY",
      "Retail",
    ],
    required: ["OLD PART NUMBER", "NEW PART NUMBER", "EN name", "CN name"],
    partNumberColumn: "NEW PART NUMBER",
  },
  // Variant 3: EPA / U8-style sheets (sheet name like "AT110 EPA").
  // "U8 Code" is the canonical part number; "Model" denormalises the
  // vehicle code into each row (we ignore it here — sheet name + fitment
  // resolver give us the same info).
  {
    kind: "chassis_u8",
    columns: [
      "No.",
      "U8 Code",
      "Model",
      "EN name",
      "CN name",
      "Specifications in CN",
      "Qty/vehicle",
      "Dealer",
      "QTY",
      "Retail",
    ],
    required: ["No.", "U8 Code", "EN name", "CN name"],
    partNumberColumn: "U8 Code",
  },
];

export interface DetectedSection {
  /** "chassis" | "engine" | ... — matched signature kind. */
  kind: HeaderSignature["kind"];
  /** The signature that matched. */
  signature: HeaderSignature;
  /** 1-based row index of the header row. */
  headerRowIndex: number;
  /** 1-based row index of the first data row. */
  dataStartIndex: number;
  /** 1-based row index of the last data row (inclusive). */
  dataEndIndex: number;
  /**
   * Free-text section title — best effort. Looks for the nearest non-empty
   * non-header row above the header. May be undefined if the section has
   * no labelled title.
   */
  title?: string;
  /**
   * Column index map: signature column name → 1-based column index in the
   * sheet. Lets row-normalizer fetch cells positionally even when the
   * source order doesn't perfectly match the canonical signature.
   */
  columnMap: ReadonlyMap<string, number>;
}

/**
 * Scan rows, detect section boundaries, and return an array of sections.
 *
 * Algorithm:
 *   1. For each row, try to match against every signature.
 *   2. If a row matches, mark it as a section header.
 *   3. Section i's data spans (header[i] + 1) .. (header[i+1] - 1).
 *   4. The title is the nearest non-empty row above the header that isn't a
 *      column-label cell.
 */
export function detectSections(rows: ReadonlyArray<SheetRow>): DetectedSection[] {
  // Index header rows.
  const headers: Array<{ row: SheetRow; sig: HeaderSignature; columnMap: Map<string, number> }> = [];
  for (const row of rows) {
    const match = matchHeader(row);
    if (match) headers.push({ row, sig: match.signature, columnMap: match.columnMap });
  }

  if (headers.length === 0) return [];

  const sections: DetectedSection[] = [];
  for (let i = 0; i < headers.length; i++) {
    const current = headers[i]!;
    const next = headers[i + 1];

    const dataStart = current.row.rowIndex + 1;
    const dataEnd = next ? next.row.rowIndex - 1 : rows[rows.length - 1]!.rowIndex;

    const title = findTitleAbove(rows, current.row.rowIndex);

    sections.push({
      kind: current.sig.kind,
      signature: current.sig,
      headerRowIndex: current.row.rowIndex,
      dataStartIndex: dataStart,
      dataEndIndex: dataEnd,
      ...(title ? { title } : {}),
      columnMap: current.columnMap,
    });
  }

  return sections;
}

/**
 * Try to match a row against any known signature.
 * Returns the matched signature + column-index map, or null if no match.
 */
export function matchHeader(
  row: SheetRow,
): { signature: HeaderSignature; columnMap: Map<string, number> } | null {
  const trimmedCells = row.values.map((v) => trimCellLabel(v));

  for (const sig of SIGNATURES) {
    const cm = new Map<string, number>();
    let allRequiredPresent = true;

    // Map signature columns to cell positions (1-based).
    for (const col of sig.columns) {
      const idx = trimmedCells.findIndex((c) => c === col);
      if (idx > -1) cm.set(col, idx);
    }

    // Check required columns are all mapped.
    for (const r of sig.required) {
      if (!cm.has(r)) {
        allRequiredPresent = false;
        break;
      }
    }
    if (allRequiredPresent) return { signature: sig, columnMap: cm };
  }

  return null;
}

/**
 * Find the section title for a given header row.
 *
 * Look at up to MAX_LOOKBACK rows immediately above the header. A "title"
 * row is a row that:
 *   • Is not itself a recognised header.
 *   • Has at most TITLE_MAX_FILLED_CELLS non-empty cells (titles are sparse).
 *   • First cell is a string (titles are labels, not numbers/part numbers).
 *
 * If no such row is found in the lookback window, returns undefined rather
 * than reaching back into the previous section's data.
 */
const MAX_LOOKBACK = 4;
const TITLE_MAX_FILLED_CELLS = 3;

function findTitleAbove(
  rows: ReadonlyArray<SheetRow>,
  headerRowIndex: number,
): string | undefined {
  // Build a quick index so we can look up by rowIndex.
  const byIndex = new Map<number, SheetRow>();
  for (const r of rows) byIndex.set(r.rowIndex, r);

  for (let offset = 1; offset <= MAX_LOOKBACK; offset++) {
    const candidate = byIndex.get(headerRowIndex - offset);
    if (!candidate) continue;
    if (matchHeader(candidate)) return undefined; // hit another header → no title for this section
    const filled = candidate.values.filter(
      (v) => v !== null && v !== undefined && String(v).trim() !== "",
    );
    if (filled.length === 0) continue; // empty row, keep walking
    if (filled.length > TITLE_MAX_FILLED_CELLS) continue; // dense row = data, not title
    // Title should be a string. Numbers / part-numbers in cell 1 → not a title.
    const first = firstNonEmpty(candidate.values);
    if (first === null) continue;
    if (looksLikePartNumberOrCallout(first)) continue;
    return first;
  }
  return undefined;
}

function looksLikePartNumberOrCallout(s: string): boolean {
  // Part numbers in this OEM: "602006-0015", "404011-0014", "1-1", "1-6L"
  // Calliouts: bare numbers like "1", "1.0", or sub like "1-1"
  if (/^\d+(\.\d+)?$/.test(s)) return true;
  if (/^\d+-\d+[A-Z]?$/.test(s)) return true;
  if (/^\d{3,6}-\d{4,}/.test(s)) return true;
  return false;
}

function firstNonEmpty(values: ReadonlyArray<CellValue>): string | null {
  for (const v of values) {
    if (v === null || v === undefined) continue;
    const s = String(v).trim();
    if (s) return s;
  }
  return null;
}

/** Trim + normalise a cell value to a comparable string label. Handles RichText. */
function trimCellLabel(v: CellValue): string {
  return cellToString(v) ?? "";
}
