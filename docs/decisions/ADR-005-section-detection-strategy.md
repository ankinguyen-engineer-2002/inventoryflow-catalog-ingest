# ADR-005: Section detection via header-regex (not row-index)

## Status
Accepted — 2026-05-11

## Context

Each parts sheet in the xlsx is **multi-section**: 10–20 sections per sheet, each section consisting of:

- a schematic image (anchored to a row in `xl/drawings/drawingN.xml`)
- a section title row (free-form text)
- a header row (`No. | Part Number | EN name | CN name | …`)
- ~10–50 data rows
- one or more blank rows separating from the next section

The header row repeats. Concretely in `FOXStorm 70 AY70-2`:

```
R15  : header  →  section "Handlebars" data R16–R26
R43  : header  →  section "Front Brake" data R44–R59
R74  : header  →  section "Rear Brake" data R75–R85
R98  : header  →  section "Front Fork" data R99–R101
R125 : header  →  section "Steering" data R126–R138
... (15+ more sections)
```

Different sheets have different total section counts and row-indices. Hard-coding row offsets won't work.

Three options:

- **A. Hard-code row indices per sheet** — fragile, requires touching code for every new dealer.
- **B. Header-row regex detection** — scan rows; any row matching the header signature is a section boundary.
- **C. Heuristic-based section breaks** (e.g., "blank row count + non-numeric first column") — works but brittle on edge cases.

## Decision

**Header-regex detection** with a Zod-validated header signature.

```ts
// Pseudocode
const CHASSIS_HEADER = ['No.', 'Part Number', 'EN name', 'CN name',
                        'Specifications in CN', 'Qty/vehicle', 'Dealer',
                        'QTY', 'Retail'];

const ENGINE_HEADER = ['No.', 'OLD PART NUMBER', 'NEW PART NUMBER',
                       'EN name', 'CN name', 'Qty/vehicle', 'Dealer',
                       'QTY', 'Retail'];

function detectSections(rows: Row[]): Section[] {
  const headerRows = rows
    .map((r, i) => ({ i, signature: trimmedSignature(r) }))
    .filter(r => matchesHeader(r.signature, [CHASSIS_HEADER, ENGINE_HEADER]));

  return headerRows.map((hr, idx) => ({
    titleRowIdx: findPrecedingTitleRow(rows, hr.i),
    headerRowIdx: hr.i,
    dataStartIdx: hr.i + 1,
    dataEndIdx: (headerRows[idx + 1]?.i ?? rows.length) - 1,
    headerKind: classifyHeader(hr.signature),
  }));
}
```

Header signatures are externalised to `src/ingest/header-signatures.yaml` so adding a new dealer's schema is a config edit, not a code change.

## AI suggestion vs my override

**Claude initially suggested** option C: detect section breaks via "2+ consecutive blank rows".

**I overrode** because:

1. **Blank-row heuristic fails empirically**: some sections in `Bull 200 AU200 (2019-2022)` have a single blank row separator; others have three. The heuristic generates false positives and false negatives unpredictably.
2. **Header regex is self-documenting**: the signature *is* the schema. Looking at `header-signatures.yaml` tells you what schemas the parser supports.
3. **Schema drift is a real concern**: when a new dealer arrives with a slightly different header (`Part No.` vs `Part Number`), the failure mode is clear — the parser logs "unrecognized header at sheet X row Y" instead of silently misclassifying rows.
4. **Config-driven enables new-dealer onboarding without code deploys** — a senior engineer building for "100s of dealerships" (JD wording) avoids any code path that requires a deploy per dealer.

## Trade-offs accepted

- **Header must match exactly (after normalisation)**: any new variant must be added to YAML. Mitigated by fuzzy match warning ("header partially matches; missing column 'X'") that triggers human review.
- **Title rows are heuristic**: assumed to be 1–2 rows above the header. A free-text scan picks "last non-empty row before header that isn't a column name". Works on samples seen; flagged for review otherwise.
- **Multi-line section titles** (rare but possible) collapse to the last line. Acceptable; recoverable via re-parsing.
- **Headers split across two rows** (some Excel templates do this) — not supported in v1. Flag in `QUESTIONS_FOR_RECRUITER.md` if encountered.

## When to revisit

- If false-positive header matches start happening (LLM-translated headers accidentally matching real header rows), add a "data row count" sanity check (real sections have >2 data rows).
- If dealer schemas diverge so much that the YAML approaches 50+ variants, introduce a schema-inference pre-pass via LLM on first-encounter dealer files.

## Sources

- Empirical probe of 5 sample sheets (`FOXStorm 70 AY70-2`, `AT110 EPA`, `Predator125 AT125-2`, `Bull 200 AU200 (2019-2022)`, `K2`) — header signatures stable within those.
- ETL pattern reference: "Schema on read" vs "schema on write" debates in Kleppmann's *Designing Data-Intensive Applications* (Ch. 4).
- Comparable approach in Apache Tika's xlsx parser: header detection by row signature, not by row index.
