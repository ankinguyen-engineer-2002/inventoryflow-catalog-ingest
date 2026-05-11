# Submission — InventoryFlow Senior Engineer Take-home

**Author:** Aric Nguyen · `aricnguyen.analytics2002@gmail.com`
**Date:** 2026-05-11
**Repo:** [github.com/ankinguyen-engineer-2002/inventoryflow-catalog-ingest](https://github.com/ankinguyen-engineer-2002/inventoryflow-catalog-ingest)

---

## What the test asked for

1. Parse a messy 241 MB OEM Excel catalog (110 sheets, 1,586 schematic images, English + Chinese)
2. Output a clean PostgreSQL database with `part_number`, `name_en`, `name_cn`
3. Upload schematic images to a Cloudflare R2 bucket
4. Include a JSON column listing every `(year, make, model)` each part fits
5. Demonstrate AI tooling (Cursor or Vision LLMs) on the parsing task

## What was delivered

| Requirement                                  | Delivered                                                       |
| -------------------------------------------- | --------------------------------------------------------------- |
| Clean database                               | PostgreSQL 16 with 12 tables, 3,938 distinct products           |
| `part_number`, `name_en`, `name_cn`          | Present on every row, properly normalised                       |
| Schematic images on R2-compatible storage    | 382 distinct objects, SHA-256-keyed, idempotent re-upload       |
| JSONB fitment column                         | Indexed via `GIN jsonb_path_ops`, `@>` lookups under 50 ms      |
| AI tooling demonstrated                      | `ILLMProvider` abstraction with five implementations + cache    |

## How to run it (three commands)

```bash
git clone https://github.com/ankinguyen-engineer-2002/inventoryflow-catalog-ingest.git
cd inventoryflow-catalog-ingest/track-a-jd-native

cp .env.example .env
docker-compose up -d
pnpm install
pnpm db:migrate

# Place the source xlsx at ../shared/sample-data/example.xlsx
pnpm ingest:full ../shared/sample-data/example.xlsx
```

Wall-time on Apple M2: approximately 60 seconds. External cost: zero — the LLM cache is committed to the repository.

## Verification

```sql
-- The test's stated focus: query parts fitting a specific vehicle
SELECT part_number, name_en, name_cn
FROM products
WHERE fitment @> '[{"make":"Kayo","model_code":"AY70-2"}]'
LIMIT 10;
```

Inspect schematic images: open <http://localhost:9001> (`minioadmin` / `minioadmin`), browse the `catalog` bucket.

## Test coverage

32 unit tests across 5 files, all passing. Includes header detection, fitment parsing, row normalisation, LLM cache hit/miss behaviour. Run with `pnpm test`.

---

## What's also in this repository (optional reading)

The submission includes additional artifacts beyond the minimum. These exist because the role is senior-positioned and architectural judgment requires demonstration. None of them are dependencies for verifying the test outputs above.

- **[`docs/TRACK_A.md`](./docs/TRACK_A.md)** — Single-document technical reference covering architecture, schema, configuration, scaling strategy, free-tier deployment patterns, and trade-offs.
- **[`PLAN.md`](./PLAN.md)** — Engineering plan that frames the two-track approach (current implementation plus documented migration target).
- **[`docs/COMPARISON.md`](./docs/COMPARISON.md)** — Trade-off matrix between Track A (delivered) and Track B (a Polars + Iceberg + Dagster lakehouse target documented for the 500+ dealer scaling threshold; not implemented in this submission).
- **[`docs/decisions/`](./docs/decisions/)** — Fourteen Architecture Decision Records capturing each non-obvious choice with the rejected alternative and rationale.
- **[`docs/QUESTIONS_FOR_RECRUITER.md`](./docs/QUESTIONS_FOR_RECRUITER.md)** — Five open questions and eight signals identified while parsing the source data (paste errors in the test PDF, ambiguous fitment encoding, etc.).

Total written documentation runs to approximately 5,000 lines. The implementation code itself is approximately 2,500 lines of TypeScript.

## Why this volume of documentation

The test specification states: *"Clean Architecture: How you structure the final Database Schema, especially the JSON Fitment column, shows us your understanding of catalog architectures."*

The JD states: *"We care far more about ownership, speed, and technical judgment than credentials."*

Architectural judgment is invisible in code alone — clean code can be produced by junior engineers given enough time. The judgment artifacts (ADRs, comparison matrix, scaling roadmap) exist to make the reasoning behind each choice auditable.

Reviewers prioritising fast evaluation can read this document and run the three commands above (approximately 15 minutes total). Reviewers prioritising depth can read TRACK_A.md (approximately 45 minutes). Both paths produce a working pipeline against the source file.

## Honest assessment

| Dimension                                              | Result                                  |
| ------------------------------------------------------ | --------------------------------------- |
| Test specification outputs                             | All five delivered                      |
| Required stack (TypeScript, Node, PG, Redis, Docker)   | Exact match                             |
| AI tooling integration                                 | Five providers, cache, audit mode       |
| Implementation runtime                                 | Approximately 60 seconds end-to-end     |
| Reviewer-side cost                                     | Zero (no API key required)              |
| Idempotent re-runs                                     | Yes (`NULLS NOT DISTINCT` unique index) |
| Test coverage                                          | 32 unit tests, all passing              |
| Production readiness gaps                              | CI/CD pipeline, distributed tracing exporter, RLS activation — documented in TRACK_A.md §1.3 |

## Contact

Available for live walkthrough, system-design deep dive, or technical interview at the convenience of the hiring team.

Aric Nguyen · `aricnguyen.analytics2002@gmail.com`
