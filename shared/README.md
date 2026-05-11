# Shared resources

Reused across Track A and Track B.

## Layout

```
shared/
├── sample-data/        Local copy of the test xlsx (NOT committed, see below)
├── prompts/            Canonical LLM prompt templates (versioned)
├── schemas/            JSON schemas / OpenAPI fragments (e.g., fitment.schema.json)
├── fixtures/           Frozen test fixtures (golden outputs)
└── llm-cache.sqlite    Committed SQLite cache for LLM responses (deliberately tracked)
```

## Sample data

The 241 MB `Copy of Example Data for Engineer.xlsx` is **not committed** (oversized + git LFS overhead). Place it at:

```
shared/sample-data/example.xlsx
```

For the reviewer: the file is the same one Talemy provided in the test packet.

## Prompts

Each LLM use case has a versioned prompt template:

- `prompts/translate-cn-en/v1.txt`
- `prompts/extract-callouts/v1.txt`
- `prompts/infer-make/v1.txt`

Version bumps invalidate cache entries — intentional. See ADR-007.

## Schemas

JSON Schema documents that govern data interchange:

- `schemas/fitment.schema.json` — the shape of a single fitment entry inside `products.fitment[]`
- `schemas/handoff-task.schema.json` — shape of entries in `handoff/translation_tasks.json`
- `schemas/handoff-result.schema.json` — shape of entries in `handoff/translation_results.json`

## LLM cache

`llm-cache.sqlite` is **committed by design**. It allows the reviewer to run the pipeline end-to-end with `LLM_PROVIDER=cached` and zero API key. See [ADR-007](../docs/decisions/ADR-007-llm-provider-cost-strategy.md) for the full rationale.

Schema:

```sql
CREATE TABLE cache (
  cache_key            TEXT PRIMARY KEY,    -- sha256(prompt + image_sha256 + template_ver)
  provider             TEXT NOT NULL,
  prompt_template_ver  TEXT NOT NULL,
  response_json        TEXT NOT NULL,
  tokens_in            INT,
  tokens_out           INT,
  cost_usd             REAL,
  latency_ms           INT,
  created_at           TEXT NOT NULL
);
```
