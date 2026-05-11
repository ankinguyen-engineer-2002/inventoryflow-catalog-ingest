# Questions for Recruiter / Assumptions

> Reads like a checklist: top section = needs answer, middle = assumptions I'm proceeding on (reversible), bottom = things I caught while reading the test.

---

## Needs answer (5)

### Q1 — Paste error in the PDF test?

The test PDF "What You'll Do" section ends with four bullets that read like a marketing JD, not engineering:

> - Maintain a content and posting calendar; ensure timelines are met
> - Coordinate with designers, copywriters, content creators, PR teams, and media contacts
> - Manage outreach, scheduling, and follow-ups with collaborators and partners
> - Ensure the founder's personal brand presence stays polished and consistent

I'm assuming this is a paste-error from a different role's JD and the engineering scope is the rest of the document (parse messy xlsx → clean DB + R2 + JSONB fitment). **Confirm?**

### Q2 — Should `make = "Kayo"` be hard-coded?

The make is **nowhere in the data file**. I'm inferring "Kayo" from model code prefixes (AY/AT/AU/K/T/S/eA). Production won't be Kayo-only; every dealer ships a different OEM.

My current design: per-dealer config (`rules.yaml`) where `make` is set, with the pipeline falling back to LLM inference if unset.

**Question**: For this test, should I hard-code Kayo, or implement the per-dealer config path?

### Q3 — R2 credentials for submission?

The test mentions Cloudflare R2. For the submission I'm using **MinIO** locally as a drop-in R2 (same S3 SDK). No Cloudflare account needed to run the demo.

**Options for review**:
1. Use MinIO; you read the code and verify the S3 client is R2-compatible.
2. You provide R2 sandbox credentials and I'll redeploy against R2.
3. I create a Cloudflare R2 trial account on my side and share a short-lived presigned URL.

**Which do you prefer?**

### Q4 — Sub-assemblies (`"1-1"`, `"1-6L"`) — flat or nested?

Some rows have `No. = "1-1"` or `"1-6L"` — these are sub-components of part `1` or the left variant of part `1-6`. Two modelling options:

- **Flat (current choice)**: each sub-component is its own `products` row with a `parent_part_number_norm` FK. Pros: queryable, marketplace-friendly, denormal joins for assembly hierarchies. Cons: shape of fitment per sub-component must be inferred from parent.
- **Nested**: keep sub-components in a JSONB `children` field on the parent row. Pros: 1 row per visible callout. Cons: not queryable by sub part number directly.

I'm proceeding with **flat**. Confirm?

### Q5 — Reference sheets (Carburetor Jets, Spark Plugs, etc.) — primary catalog or separate?

About 12 sheets have a totally different shape (cross-reference tables rather than parts catalogs). My plan:

- Ingest into a separate `reference_specs` table with category + JSONB attributes (free-form).
- Keep `products` clean and parts-only.

Confirm this matches your mental model?

---

## Assumptions I'm proceeding on (reversible)

1. `Sheet18` (empty sheet in the workbook) is residue; skip with a logged warning. ✓
2. Variant suffixes like `EPA`, `EFI`, `D` are model-level attributes, not SKU-level. Stored in `vehicle_models.variant`. ✓
3. `OLD PART NUMBER` rows on engine sheets are not separate products — they're aliases. Fold into `part_number_aliases` table with `alias_type='old'`. ✓
4. `Specifications in CN` is kept verbatim in `products.spec_cn`. **Not translated** unless explicitly asked. ✓
5. The 1586 embedded images are JPEG/PNG — store as-is (no re-encoding) in R2 keyed by SHA-256. ✓
6. Sheet names with trailing whitespace get normalised (trim) for keying but the original is preserved in `products.source_sheet`. ✓
7. Category mapping from TOC (SPORT_ATV, UTILITY_ATV, PITBIKE_EPA, etc.) is stored on `vehicle_models.category`, not duplicated on every fitment entry. ✓
8. Pricing fields (`Dealer`, `Retail`) are kept as numeric but flagged as **dealer-supplied** in `data_quality` JSONB — marketplace pricing happens elsewhere. ✓

---

## Things I caught while reading the test (signal)

These aren't questions — they're flags showing I read the data, not just the test:

1. **`xl/drawings/drawingN.xml`** is the only way to map images to sheet rows. `exceljs` and `openpyxl` do not expose this binding directly. Anyone who claims they "uploaded the images" without parsing this XML is uploading the right files anchored to the wrong rows.

2. **Header repetition within sheets**. Sheets like `FOXStorm 70 AY70-2` repeat the header row (`No. | Part Number | EN name | CN name | ...`) at R15, R43, R74, R98, R125, ... — each marks a new section with its own schematic image. Section detection must be dynamic; you cannot iterate from row 16 assuming a single table.

3. **Engine sheets have a different schema** from chassis sheets — they include `OLD PART NUMBER` + `NEW PART NUMBER` columns and omit `Specifications in CN`. A parser that assumes one schema across all sheets misses ~50% of the file.

4. **Encoding artifacts** in CN cells (`"军  绿"` has two spaces inside the string) — must be normalised before hash-keying or LLM-translation cache misses fire on every read.

5. **TOC has fitment hints** — it groups sheets into 8 categories (SPORT_ATV, UTILITY_ATV, PITBIKE_COMPETITION, PITBIKE_EPA, DIRTBIKE_COMPETITION, DIRTBIKE_EPA, SSV, ELECTRIC). This is more reliable than parsing sheet name strings for category.

6. **Same `No.` ≠ duplicate** — multiple rows with the same callout number are SKU variants (e.g., `"black handle bar grip"` vs `"black handle bar grip(9.26.2022~)"` — color/date-effective). De-duplicating on `No.` is a bug; de-duplicating on `Part Number` is correct.

7. **Year ranges encode "open-ended" semantics** — `"(2023+)"` means year_start=2023, year_end=NULL. A schema with two non-null INT year columns must allow NULL on year_end.

8. **`Sheet18` is empty** but isn't hidden — easy to silently skip and never notice. Logging a warning is the right behaviour.
