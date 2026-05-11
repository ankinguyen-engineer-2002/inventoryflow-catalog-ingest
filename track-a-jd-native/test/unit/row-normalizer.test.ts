import { describe, expect, it } from "vitest";
import { normaliseRow, parseCallout } from "../../src/ingest/row-normalizer.js";
import type { DetectedSection } from "../../src/ingest/section-detector.js";
import { SIGNATURES } from "../../src/ingest/section-detector.js";
import type { SheetRow } from "../../src/ingest/xlsx-reader.js";

function makeChassisSection(): DetectedSection {
  const sig = SIGNATURES.find((s) => s.kind === "chassis")!;
  const cm = new Map<string, number>();
  sig.columns.forEach((c, i) => cm.set(c, i + 1));
  return {
    kind: "chassis",
    signature: sig,
    headerRowIndex: 15,
    dataStartIndex: 16,
    dataEndIndex: 100,
    columnMap: cm,
  };
}

function row(rowIndex: number, ...values: Array<string | number | null>): SheetRow {
  return { rowIndex, values: [null, ...values] };
}

describe("parseCallout", () => {
  it("parses integer callouts", () => {
    expect(parseCallout(1)).toEqual({ raw: "1", parent: 1, sub: null, variant: null });
    expect(parseCallout("23")).toEqual({ raw: "23", parent: 23, sub: null, variant: null });
  });

  it("parses '1.0' as parent 1", () => {
    expect(parseCallout("1.0")).toEqual({ raw: "1.0", parent: 1, sub: null, variant: null });
  });

  it("parses sub-callouts '1-6'", () => {
    expect(parseCallout("1-6")).toEqual({ raw: "1-6", parent: 1, sub: 6, variant: null });
  });

  it("parses left/right variant '1-6L'", () => {
    expect(parseCallout("1-6L")).toEqual({ raw: "1-6L", parent: 1, sub: 6, variant: "L" });
    expect(parseCallout("1-6R")).toEqual({ raw: "1-6R", parent: 1, sub: 6, variant: "R" });
  });

  it("returns null on null / empty / whitespace", () => {
    expect(parseCallout(null)).toBeNull();
    expect(parseCallout("")).toBeNull();
    expect(parseCallout("  ")).toBeNull();
  });
});

describe("normaliseRow", () => {
  const section = makeChassisSection();

  it("normalises a typical chassis row", () => {
    const r = row(16, 1, "602006-0015", "black grip", "把套", "spec text", 2, 6, "/ea", 10.2);
    const result = normaliseRow(r, section, "FOXStorm 70 AY70-2 ");
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.row.partNumber).toBe("602006-0015");
    expect(result.row.nameEn).toBe("black grip");
    expect(result.row.nameCn).toBe("把套");
    expect(result.row.callout?.parent).toBe(1);
    expect(result.row.qtyPerVehicle).toBe(2);
    expect(result.row.dealerCost).toBe(6);
    expect(result.row.retailPrice).toBe(10.2);
    expect(result.row.sourceSheet).toBe("FOXStorm 70 AY70-2");
  });

  it("rejects a row with no part number", () => {
    const r = row(17, 1, null, "name", "名", "spec", 1, 5, "/ea", 8.5);
    const result = normaliseRow(r, section, "TestSheet");
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.reason).toBe("missing_part_number");
  });

  it("collapses multi-space artifacts in CN cells", () => {
    const r = row(18, 1, "P1", "name", "把套", "军  绿  色", 1, 5, "/ea", 8.5);
    const result = normaliseRow(r, section, "TestSheet");
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.row.specCn).toBe("军 绿 色");
  });

  it("preserves null when EN name is missing", () => {
    const r = row(19, 2, "P2", null, "把套", null, 1, 5, "/ea", 8.5);
    const result = normaliseRow(r, section, "TestSheet");
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.row.nameEn).toBeNull();
  });

  it("coerces non-numeric price cells to null gracefully", () => {
    const r = row(20, 3, "P3", "name", "名", "spec", "NA", "—", "/ea", "TBD");
    const result = normaliseRow(r, section, "TestSheet");
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.row.qtyPerVehicle).toBeNull();
    expect(result.row.dealerCost).toBeNull();
    expect(result.row.retailPrice).toBeNull();
  });
});
