/**
 * Row normaliser.
 *
 * Converts raw exceljs cell tuples into validated `NormalisedRow` shapes
 * ready for persistence. Encodes our tolerance for messy OEM data:
 *   • `No.` polymorphism (number, "1-1", null) → parsed parts
 *   • Pricing fields are best-effort numerics; non-numeric → null
 *   • Empty strings normalised to null
 *   • Multi-space artifacts in CN names ("军  绿") collapsed
 *
 * Validation surface = Zod schema. Failed rows return `{ ok: false }`
 * with an error description; caller decides DLQ vs skip.
 */
import { z } from "zod";
import type { SheetRow, CellValue } from "./xlsx-reader.js";
import type { DetectedSection } from "./section-detector.js";
import { cellToString, cellToNumber } from "./cell-utils.js";

/* ───────────────────────────────────────────────────────────────────
 * Output shape
 * ───────────────────────────────────────────────────────────────────*/

/** Parsed "No." callout — captures sub-part + left/right variant. */
export interface CalloutNumber {
  /** The raw cell value, normalised to string. */
  raw: string;
  /** Parent number — "1" for "1-1", "1-6L". */
  parent: number | null;
  /** Sub-number — null for "1.0", 1 for "1-1", 6 for "1-6L". */
  sub: number | null;
  /** Variant suffix — null for "1-1", "L" for "1-6L", "R" for "1-6R". */
  variant: string | null;
}

export interface NormalisedRow {
  /** 1-based sheet row index for provenance. */
  sourceRowIndex: number;
  /** Sheet name (trimmed) for provenance. */
  sourceSheet: string;
  /** Parsed callout, or null when the row was unnumbered. */
  callout: CalloutNumber | null;
  /** Canonical part number (case-preserved, trimmed). Empty → row rejected. */
  partNumber: string;
  /** Optional "OLD PART NUMBER" alias (engine sheets only). */
  partNumberAlias: string | null;
  nameEn: string | null;
  nameCn: string | null;
  specCn: string | null;
  qtyPerVehicle: number | null;
  dealerCost: number | null;
  unit: string | null;
  retailPrice: number | null;
}

export type NormalisedResult =
  | { ok: true; row: NormalisedRow }
  | { ok: false; reason: string; rowIndex: number };

/* ───────────────────────────────────────────────────────────────────
 * Public API
 * ───────────────────────────────────────────────────────────────────*/

/**
 * Normalise one data row from a section.
 * The section's `columnMap` is used to pick cells positionally.
 */
export function normaliseRow(
  row: SheetRow,
  section: DetectedSection,
  sheetName: string,
): NormalisedResult {
  const get = (col: string): CellValue => {
    const idx = section.columnMap.get(col);
    if (idx === undefined) return null;
    return row.values[idx] ?? null;
  };

  // Part number column varies by schema kind — the signature tells us.
  const partNumber =
    cleanString(get(section.signature.partNumberColumn)) ??
    // Engine fallback: NEW missing → OLD.
    (section.kind === "engine" ? cleanString(get("OLD PART NUMBER")) : null);

  if (!partNumber) {
    return { ok: false, rowIndex: row.rowIndex, reason: "missing_part_number" };
  }

  const partNumberAlias =
    section.kind === "engine" ? cleanString(get("OLD PART NUMBER")) : null;

  const out: NormalisedRow = {
    sourceRowIndex: row.rowIndex,
    sourceSheet: sheetName.trim(),
    callout: parseCallout(get("No.")),
    partNumber,
    partNumberAlias: partNumberAlias && partNumberAlias !== partNumber ? partNumberAlias : null,
    nameEn: cleanString(get("EN name")),
    nameCn: cleanCnString(get("CN name")),
    specCn: cleanCnString(get("Specifications in CN")),
    qtyPerVehicle: toNumberOrNull(get("Qty/vehicle")),
    dealerCost: toNumberOrNull(get("Dealer")),
    unit: cleanString(get("QTY")),
    retailPrice: toNumberOrNull(get("Retail")),
  };

  return { ok: true, row: out };
}

/* ───────────────────────────────────────────────────────────────────
 * Callout parsing
 * ───────────────────────────────────────────────────────────────────*/

const CALLOUT_PLAIN_RE = /^(\d+)(?:\.0+)?$/;
const CALLOUT_SUB_RE = /^(\d+)-(\d+)([A-Z]?)$/i;

export function parseCallout(v: CellValue): CalloutNumber | null {
  if (v === null || v === undefined) return null;

  const raw = String(v).trim();
  if (!raw) return null;

  // "1", "1.0", "23"
  const plain = raw.match(CALLOUT_PLAIN_RE);
  if (plain) {
    return { raw, parent: Number(plain[1]), sub: null, variant: null };
  }

  // Numeric (already a number type)
  if (typeof v === "number" && Number.isFinite(v)) {
    return { raw, parent: Math.trunc(v), sub: null, variant: null };
  }

  // "1-1", "1-6L", "1-6R"
  const sub = raw.match(CALLOUT_SUB_RE);
  if (sub) {
    return {
      raw,
      parent: Number(sub[1]),
      sub: Number(sub[2]),
      variant: sub[3] ? sub[3].toUpperCase() : null,
    };
  }

  // Unparseable but non-empty — keep raw, parent unknown
  return { raw, parent: null, sub: null, variant: null };
}

/* ───────────────────────────────────────────────────────────────────
 * Cell value coercion helpers
 * ───────────────────────────────────────────────────────────────────*/

function cleanString(v: CellValue): string | null {
  return cellToString(v);
}

function cleanCnString(v: CellValue): string | null {
  const s = cleanString(v);
  if (s === null) return s;
  // Collapse internal multi-spaces seen in CN cells ("军  绿" → "军 绿"),
  // but preserve single spaces between distinct words.
  return s.replace(/\s{2,}/g, " ");
}

function toNumberOrNull(v: CellValue): number | null {
  return cellToNumber(v);
}

/* ───────────────────────────────────────────────────────────────────
 * Zod schema (exported for use by external consumers like API webhooks)
 * ───────────────────────────────────────────────────────────────────*/

export const NormalisedRowSchema = z.object({
  sourceRowIndex: z.number().int().positive(),
  sourceSheet: z.string().min(1),
  callout: z
    .object({
      raw: z.string(),
      parent: z.number().int().nullable(),
      sub: z.number().int().nullable(),
      variant: z.string().nullable(),
    })
    .nullable(),
  partNumber: z.string().min(1),
  partNumberAlias: z.string().nullable(),
  nameEn: z.string().nullable(),
  nameCn: z.string().nullable(),
  specCn: z.string().nullable(),
  qtyPerVehicle: z.number().nullable(),
  dealerCost: z.number().nullable(),
  unit: z.string().nullable(),
  retailPrice: z.number().nullable(),
});
