/**
 * Unit tests for fitment-resolver.
 *
 * Sheet name → ResolvedFitment. Year ranges, open-ended ranges,
 * variant detection, model code extraction.
 */
import { describe, expect, it } from "vitest";
import { resolveFitmentFromSheetName } from "../../src/ingest/fitment-resolver.js";

describe("resolveFitmentFromSheetName", () => {
  it("parses bounded year range '(2016-2020)'", () => {
    // Sheet name "PREDATOR 125 (2016-2020)" in the actual workbook carries
    // year info but no explicit model code prefix. modelCode is null in
    // that case — the caller should fall back to dealer config to fill it.
    const r = resolveFitmentFromSheetName("PREDATOR 125 (2016-2020)");
    expect(r?.yearStart).toBe(2016);
    expect(r?.yearEnd).toBe(2020);
    expect(r?.modelCode).toBeNull();
  });

  it("parses bounded year + model code when both present", () => {
    const r = resolveFitmentFromSheetName("Bull 180 AU180 (2020-2022)");
    expect(r?.yearStart).toBe(2020);
    expect(r?.yearEnd).toBe(2022);
    expect(r?.modelCode).toBe("AU180");
  });

  it("parses open-ended year '(2023+)'", () => {
    const r = resolveFitmentFromSheetName("AU180-2 (2023+) Parts Diagram");
    expect(r?.yearStart).toBe(2023);
    expect(r?.yearEnd).toBeNull();
    expect(r?.modelCode).toBe("AU180-2");
  });

  it("parses year prefix '2024+'", () => {
    const r = resolveFitmentFromSheetName("2024+ TT125 EPA");
    expect(r?.yearStart).toBe(2024);
    expect(r?.yearEnd).toBeNull();
    expect(r?.modelCode).toBe("TT125");
    expect(r?.variant).toBe("EPA");
  });

  it("extracts model code with no year info", () => {
    const r = resolveFitmentFromSheetName("FOXStorm 70 AY70-2 ");
    expect(r?.yearStart).toBeNull();
    expect(r?.yearEnd).toBeNull();
    expect(r?.modelCode).toBe("AY70-2");
  });

  it("extracts variant separately when present", () => {
    const r = resolveFitmentFromSheetName("Bull125 AU125-D EFI");
    expect(r?.modelCode).toBe("AU125-D");
    // "D" is a model-code suffix (Direct-Injection variant); "EFI" is the
    // EPA/emissions-variant marker. Only the latter is treated as variant.
    expect(r?.variant).toBe("EFI");
  });

  it("returns null for non-fitment sheets (TOC, specs, junk)", () => {
    expect(resolveFitmentFromSheetName("TABLE OF CONTENTS")).toBeNull();
    expect(resolveFitmentFromSheetName("Sheet18")).toBeNull();
    expect(resolveFitmentFromSheetName("SPARK PLUGS")).toBeNull();
    expect(resolveFitmentFromSheetName("Carburetor Jets")).toBeNull();
    expect(resolveFitmentFromSheetName("ATV Wheel specs")).toBeNull();
  });

  it("trims trailing whitespace in sheet names", () => {
    const r = resolveFitmentFromSheetName(" FOXStorm 70 AY70-2  ");
    expect(r?.modelCode).toBe("AY70-2");
  });

  it("handles parenthesised single year", () => {
    const r = resolveFitmentFromSheetName("TD125 (2022) Engine");
    expect(r?.yearStart).toBe(2022);
    expect(r?.yearEnd).toBe(2022);
    expect(r?.modelCode).toBe("TD125");
  });
});
