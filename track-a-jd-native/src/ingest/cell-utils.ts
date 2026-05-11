/**
 * Cell-value coercion utilities shared across ingest modules.
 *
 * exceljs returns several runtime shapes depending on cell content:
 *   • string   → plain text
 *   • number   → numeric
 *   • Date     → date cell
 *   • { richText: [{text}, …] } → formatted text — common in CN cells
 *   • { formula, result } → formula cell
 *   • { hyperlink, text }  → link
 *
 * `cellToString` normalises any of these to a clean string (or null).
 */
import type { CellValue } from "./xlsx-reader.js";

export function cellToString(v: CellValue | object): string | null {
  if (v === null || v === undefined) return null;

  if (typeof v === "object" && !(v instanceof Date)) {
    const obj = v as Record<string, unknown>;

    if (Array.isArray(obj["richText"])) {
      const parts = obj["richText"] as Array<{ text?: string }>;
      const text = parts.map((p) => p.text ?? "").join("").trim();
      return text || null;
    }

    if (typeof obj["result"] === "string" || typeof obj["result"] === "number") {
      return String(obj["result"]);
    }

    if (typeof obj["text"] === "string") {
      return (obj["text"] as string).trim() || null;
    }

    // Unknown object shape — drop rather than serialise "[object Object]".
    return null;
  }

  const s = String(v).trim();
  return s.length > 0 ? s : null;
}

export function cellToNumber(v: CellValue | object): number | null {
  if (v === null || v === undefined) return null;
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "boolean") return v ? 1 : 0;

  if (typeof v === "object" && !(v instanceof Date)) {
    const obj = v as Record<string, unknown>;
    if (typeof obj["result"] === "number") return obj["result"] as number;
    return null;
  }

  const n = Number(String(v).trim());
  return Number.isFinite(n) ? n : null;
}
