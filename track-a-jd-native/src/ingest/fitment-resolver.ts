/**
 * Fitment resolver.
 *
 * Year/make/model fitment is encoded in the SHEET NAME (the data file
 * doesn't have explicit fitment columns). Examples observed in the sample:
 *
 *   "FOXStorm 70 AY70-2 "              → year_start=null, year_end=null,
 *                                         model_code="AY70-2"
 *   "PREDATOR 125 (2016-2020)"         → 2016..2020, model_code="AT125-B"
 *   "Bull 180 AU180 (2020-2022)"       → 2020..2022, model_code="AU180"
 *   "AU180-2 (2023+) Parts Diagram"    → 2023..null, model_code="AU180-2"
 *   "2024+ TT125 EPA"                  → 2024..null, model_code="TT125", variant="EPA"
 *
 * Heuristics:
 *   • Year ranges in parens: "(YYYY-YYYY)" | "(YYYY+)" | "(YYYY)"
 *   • Year prefix: "YYYY+ ..." | "YYYY ..."
 *   • Model code regex: well-known prefixes from the OEM
 *   • Variant: "EPA" | "EFI" | "D" appearing as a separate word
 *
 * Returns null when no fitment information can be extracted (eg "TABLE OF
 * CONTENTS", "Sheet18", "SPARK PLUGS"). The caller decides whether to
 * skip the sheet or treat it as a reference/spec sheet.
 */

export interface ResolvedFitment {
  yearStart: number | null;
  yearEnd: number | null;
  modelCode: string | null;
  variant: string | null;
  // We don't infer `make` here — that's a per-dealer config from `rules.yaml`.
  // The caller adds `make: "Kayo"` (or whatever) before persisting.
}

const MODEL_CODE_RE = /\b(AY|AT|AU|KMB|TS|TSD|TD|TT|K2|K4|K6|KT|T2|T4|S70|S200|S350|eA|eKMB)\d*-?\d*[A-Z]?\b/i;
const RANGE_PAREN_RE = /\((\d{4})\s*-\s*(\d{4})\)/;
const OPEN_END_PAREN_RE = /\((\d{4})\s*\+\)/;
const SINGLE_YEAR_PAREN_RE = /\((\d{4})\)/;
const OPEN_END_PREFIX_RE = /\b(\d{4})\+/;
// Variant only matches standalone "EPA" / "EFI" markers.
// "D" is ambiguous (suffix on model codes like AU125-D), so we don't
// treat it as a separate variant — it stays as part of model_code.
const VARIANT_RE = /\b(EPA|EFI)\b/i;

export function resolveFitmentFromSheetName(
  sheetName: string,
): ResolvedFitment | null {
  const name = sheetName.trim();

  // Skip non-fitment sheets fast.
  if (isNonFitmentSheet(name)) return null;

  // Year window.
  let yearStart: number | null = null;
  let yearEnd: number | null = null;

  const rangeMatch = name.match(RANGE_PAREN_RE);
  if (rangeMatch) {
    yearStart = Number(rangeMatch[1]);
    yearEnd = Number(rangeMatch[2]);
  } else {
    const openEndParen = name.match(OPEN_END_PAREN_RE);
    if (openEndParen) {
      yearStart = Number(openEndParen[1]);
      yearEnd = null;
    } else {
      const singleParen = name.match(SINGLE_YEAR_PAREN_RE);
      if (singleParen) {
        yearStart = Number(singleParen[1]);
        yearEnd = Number(singleParen[1]);
      } else {
        const openEndPrefix = name.match(OPEN_END_PREFIX_RE);
        if (openEndPrefix) {
          yearStart = Number(openEndPrefix[1]);
          yearEnd = null;
        }
      }
    }
  }

  // Model code.
  const modelMatch = name.match(MODEL_CODE_RE);
  const modelCode = modelMatch?.[0]?.toUpperCase() ?? null;

  // Variant.
  const variantMatch = name.match(VARIANT_RE);
  const variant = variantMatch?.[1]?.toUpperCase() ?? null;

  // If we got neither a year nor a model code, we don't have enough to call it fitment.
  if (yearStart === null && modelCode === null) return null;

  return { yearStart, yearEnd, modelCode, variant };
}

function isNonFitmentSheet(name: string): boolean {
  const upper = name.toUpperCase();
  return (
    upper === "TABLE OF CONTENTS" ||
    upper === "TOC" ||
    upper === "SHEET18" ||
    upper === "CARBURETOR JETS" ||
    upper === "FORK SEAL SPECS" ||
    upper === "ATV WHEEL SPECS" ||
    upper === "SPOKE SPECS" ||
    upper === "SPARK PLUGS" ||
    upper === "BATTERY SPECS" ||
    upper === "OWNERS MANUALS" ||
    upper.startsWith("DIRTBIKEPITBIKE WHEEL BEARING") ||
    upper === "SNOW TRACK KIT" ||
    upper === "SKI KIT PARTS" ||
    upper.includes("UPGRADE KIT") ||
    upper === "EC2I"
  );
}
