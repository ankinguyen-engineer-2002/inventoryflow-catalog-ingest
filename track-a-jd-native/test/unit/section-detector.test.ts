/**
 * Unit tests for section-detector.
 *
 * The trickiest pieces are:
 *   • Header signature matching with variant column ordering
 *   • Multi-section sheets where the header repeats
 *   • Polymorphic `No.` column values (1.0 / "1-1" / "1-6L" / null)
 *
 * We don't test against the real xlsx here — that's integration. We feed
 * synthetic rows shaped like what xlsx-reader produces.
 */
import { describe, expect, it } from "vitest";
import { detectSections, matchHeader, SIGNATURES } from "../../src/ingest/section-detector.js";
import type { SheetRow } from "../../src/ingest/xlsx-reader.js";

function row(rowIndex: number, ...values: Array<string | number | null>): SheetRow {
  // exceljs uses 1-based column indexing, so prepend null at index 0.
  return { rowIndex, values: [null, ...values] };
}

describe("matchHeader", () => {
  it("matches the chassis signature", () => {
    const r = row(
      15,
      "No.",
      "Part Number",
      "EN name",
      "CN name",
      "Specifications in CN",
      "Qty/vehicle",
      "Dealer",
      "QTY",
      "Retail",
    );
    const m = matchHeader(r);
    expect(m).not.toBeNull();
    expect(m?.signature.kind).toBe("chassis");
    expect(m?.columnMap.get("Part Number")).toBeGreaterThan(0);
  });

  it("matches the engine signature (OLD/NEW PART NUMBER)", () => {
    const r = row(
      21,
      "No.",
      "OLD PART NUMBER",
      "NEW PART NUMBER",
      "EN name",
      "CN name",
      "Qty/vehicle",
      "Dealer",
      "QTY",
      "Retail",
    );
    const m = matchHeader(r);
    expect(m?.signature.kind).toBe("engine");
  });

  it("matches even when only the 4 required columns are present", () => {
    // Required = ["No.", "Part Number", "EN name", "CN name"]. The rest are
    // nice-to-have. Graceful matching lets us still process sheets where
    // the dealer dropped optional columns (eg some pricing variants).
    const r = row(15, "No.", "Part Number", "EN name", "CN name", null, null);
    const m = matchHeader(r);
    expect(m?.signature.kind).toBe("chassis");
    expect(m?.columnMap.get("Specifications in CN")).toBeUndefined();
  });

  it("does not match a data row that contains the word 'No.'", () => {
    const r = row(16, 1, "602006-0015", "black grip", "把套", "spec", 2, 6, "/ea", 10.2);
    expect(matchHeader(r)).toBeNull();
  });

  it("handles trim variation in header labels", () => {
    const r = row(
      15,
      "No.  ", // trailing spaces
      " Part Number",
      "EN name",
      "CN name",
      "Specifications in CN",
      "Qty/vehicle",
      "Dealer",
      "QTY",
      "Retail",
    );
    const m = matchHeader(r);
    expect(m?.signature.kind).toBe("chassis");
  });
});

describe("detectSections", () => {
  it("returns empty array when no header row present", () => {
    const rows = [row(1, "Some title"), row(2, "Another row")];
    expect(detectSections(rows)).toEqual([]);
  });

  it("detects a single section with correct data range", () => {
    const rows: SheetRow[] = [
      row(1, "FOXStorm 70 AY70-2 Parts Diagram"),
      row(15, "No.", "Part Number", "EN name", "CN name", "Specifications in CN", "Qty/vehicle", "Dealer", "QTY", "Retail"),
      row(16, 1, "602006-0015", "grip", "把套", "spec", 2, 6, "/ea", 10.2),
      row(17, 2, "313001-0008", "switch", "组合开关", "spec2", 1, 20, "/ea", 34),
    ];
    const sections = detectSections(rows);
    expect(sections).toHaveLength(1);
    expect(sections[0]?.kind).toBe("chassis");
    expect(sections[0]?.headerRowIndex).toBe(15);
    expect(sections[0]?.dataStartIndex).toBe(16);
    expect(sections[0]?.dataEndIndex).toBe(17);
  });

  it("detects multiple sections in the same sheet", () => {
    const headerCells: Array<string | number | null> = [
      "No.",
      "Part Number",
      "EN name",
      "CN name",
      "Specifications in CN",
      "Qty/vehicle",
      "Dealer",
      "QTY",
      "Retail",
    ];
    const rows: SheetRow[] = [
      row(15, ...headerCells),
      row(16, 1, "P1", "name", "名", "spec", 2, 6, "/ea", 10),
      row(43, ...headerCells),
      row(44, 1, "P2", "name", "名", "spec", 2, 6, "/ea", 10),
      row(74, ...headerCells),
      row(75, 1, "P3", "name", "名", "spec", 2, 6, "/ea", 10),
    ];
    const sections = detectSections(rows);
    expect(sections).toHaveLength(3);
    expect(sections[0]?.dataEndIndex).toBe(42);
    expect(sections[1]?.dataStartIndex).toBe(44);
    expect(sections[1]?.dataEndIndex).toBe(73);
    expect(sections[2]?.dataStartIndex).toBe(75);
  });

  it("handles mixed chassis + engine kind detection in the same workbook (not same sheet)", () => {
    const chassisHeader: Array<string | number | null> = SIGNATURES[0]!.columns.map((c) => c);
    const engineHeader: Array<string | number | null> = SIGNATURES[1]!.columns.map((c) => c);

    const chassisRows: SheetRow[] = [row(10, ...chassisHeader), row(11, 1, "x")];
    const engineRows: SheetRow[] = [row(20, ...engineHeader), row(21, 1, "old", "new")];

    expect(detectSections(chassisRows)[0]?.kind).toBe("chassis");
    expect(detectSections(engineRows)[0]?.kind).toBe("engine");
  });
});
