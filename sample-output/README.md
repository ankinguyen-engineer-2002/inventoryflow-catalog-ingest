# Sample Output — Pre-Computed Artefacts

> Real outputs from running the Track A pipeline against the source xlsx, committed here so reviewers can inspect the standardised data shape without booting the stack.

---

## What's inside

| Path                             | Contents                                                               | Rows  |
| -------------------------------- | ---------------------------------------------------------------------- | ----- |
| `data/products-sample.csv`       | First 50 products (part_number, name_en, name_cn, retail, image_key)   | 50    |
| `data/products-full.csv`         | All products including JSONB fitment serialised as text                | 3,938 |
| `data/vehicle-models.csv`        | Vehicle dimension table derived from `products.fitment`                | 35    |
| `data/reference-specs-sample.csv`| First 100 rows of `reference_specs` (the 12 exception sheets)          | 100   |
| `data/llm-audit-results.csv`     | LLM cross-validation findings: dealer EN vs LLM EN with consensus score | 68    |
| `data/mdcp-bindings.csv`         | Metadata-driven dispatch bindings: dealer × pattern × schedule         | 3     |
| `images/`                        | 20 sample schematic images (PNG/JPG) extracted from MinIO              | 20    |
| `queries/`                       | Example SQL queries with their expected output                          | —     |

---

## How these were produced

```bash
docker-compose up -d
pnpm install && pnpm db:migrate
pnpm ingest:full ../shared/sample-data/example.xlsx
# Then exported via psql \COPY to CSV.
```

Reviewers can reproduce the full dataset by running `pnpm ingest:full` against the source xlsx. The committed sample exists for inspection without infrastructure setup.

---

## Standardisation contract

Every product row in `products-full.csv` satisfies:

- `part_number` non-null, trimmed, OEM-issued
- `name_en` non-null (translated EN name)
- `name_cn` non-null when the source row had a Chinese label
- `fitment` is a JSON array of `{year, make, model, model_code, variant, category, section, callout_no, confidence}` objects
- `primary_image_r2_key` references a SHA-256-keyed object in the catalog bucket
- `data_quality` JSONB records translation provenance and LLM audit results where applicable

This is the wire format that downstream consumers (marketplace sync, catalog API, analytics) read directly.
