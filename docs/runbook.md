# Runbook

> Operational reference. Updated as code lands.

---

## Local setup

### Prerequisites

- Docker Desktop 4.x+ (for Postgres + Redis + MinIO containers)
- Node.js 22 LTS via `nvm` or `fnm`
- pnpm 9.x
- (Track B only) Python 3.12 + Poetry 1.8+
- (Optional, for LLM cache regen) Ollama, `ollama pull qwen2-vl:7b`

### First run (Track A)

```bash
cd track-a-jd-native
cp .env.example .env
docker compose up -d
# Wait ~10s for Postgres healthcheck
pnpm install
pnpm db:migrate
pnpm ingest ../shared/sample-data/example.xlsx
```

Expected timeline:
- Docker boot: ~10 s
- Schema migrate: ~2 s
- Ingest run: 4–6 min on M2 Mac

### First run (Track B)

```bash
cd track-b-data-engineering
cp .env.example .env
docker compose up -d
poetry install
make bootstrap          # creates MinIO buckets, dbt deps
make track-b-run        # full medallion run
make track-b-query      # sample DuckDB queries
```

---

## Common operations

### Inspect an ingest run

```bash
# Latest run
psql -h localhost -U dev -d catalog -c \
  'SELECT run_id, status, rows_succeeded, rows_failed, llm_cost_usd,
          finished_at - started_at AS duration
   FROM ingest_runs ORDER BY started_at DESC LIMIT 1;'

# All runs today
psql -h localhost -U dev -d catalog -c \
  "SELECT * FROM ingest_runs WHERE started_at::date = current_date;"
```

### Find parts that fit a specific model

```bash
psql -h localhost -U dev -d catalog -c "
SELECT part_number, name_en, name_cn, fitment
FROM products
WHERE fitment @> '[{\"make\":\"Kayo\",\"model_code\":\"AY70-2\",\"year\":2022}]'
LIMIT 10;"
```

### Replay a failed ingest

```bash
# Show DLQ
pnpm dlq:list

# Retry one job
pnpm dlq:retry <job-id>

# Retry all
pnpm dlq:drain
```

### Regenerate LLM cache (for me, not reviewer)

```bash
# 1. Emit handoff tasks
LLM_PROVIDER=claude-code-handoff pnpm ingest <file>
# → writes shared/handoff/translation_tasks.json

# 2. In Claude Code:
#    "Read shared/handoff/translation_tasks.json, translate
#     each CN string to English, write shared/handoff/translation_results.json
#     keyed by id"

# 3. Re-run; provider reads results & populates cache
LLM_PROVIDER=claude-code-handoff pnpm ingest <file>

# 4. Verify cache
sqlite3 shared/llm-cache.sqlite "SELECT COUNT(*) FROM cache;"

# 5. Commit cache
git add shared/llm-cache.sqlite && git commit -m "chore(cache): refresh LLM cache"
```

---

## Troubleshooting

### `exceljs` OOM on large file

Symptom: Node process dies with `JavaScript heap out of memory` mid-parse.

Cause: `workbook.xlsx.readFile()` loads the entire workbook. Use the streaming reader.

Fix: Verify code uses `new ExcelJS.stream.xlsx.WorkbookReader(path)` not `new ExcelJS.Workbook()`. See ADR-005.

### Image upload stalls at ~200 images

Symptom: BullMQ `upload-image` queue stops draining, no errors.

Cause: R2 rate limit hit (1000 req/sec hard cap).

Fix: Lower worker concurrency in `queue/queues.ts` from 16 → 8. Verify via Cloudflare R2 dashboard. ADR-003.

### `fitment @>` query slow despite GIN index

Symptom: Query takes >500 ms.

Cause: Index using `jsonb_ops` not `jsonb_path_ops`; or query uses `?` operator instead of `@>`.

Fix: `\d products` and verify index DDL has `USING gin (fitment jsonb_path_ops)`. Use `EXPLAIN ANALYZE` to confirm index used.

### LLM cache miss on a known prompt

Symptom: Provider hits upstream despite previous run.

Cause: Prompt template version bumped → cache key changes.

Fix: Check `prompt_template_ver` in `ingest_audit`. If template changed intentionally, accept the miss; otherwise pin the version.

### Track B Polars fails on `_run_id` partition

Symptom: Delta write errors with "schema mismatch".

Cause: First run created the table without partitioning; subsequent runs try to partition.

Fix: Drop `bronze.catalog_rows` and re-run, OR run `OPTIMIZE` + `REORG` to repartition.

---

## Disaster recovery

### Postgres data corruption

```bash
# Stop ingestion
docker compose stop track-a

# Restore from last backup
docker compose exec postgres pg_restore -d catalog /backups/latest.dump

# Re-ingest from last good run
pnpm ingest --from-run <last-good-run-id>
```

### R2 object lost

If a `product_images.r2_key` no longer resolves:

```bash
# Find affected products
psql -c "SELECT id, part_number FROM product_images
         WHERE r2_key IN (SELECT r2_key FROM <lost_keys>);"

# Re-extract from xlsx + re-upload (idempotent)
pnpm reupload-images --product-ids <id1,id2,...>
```

### Track B Delta corruption

Use Delta time travel — recovery is the documented path:

```python
import polars as pl
df = pl.read_delta("s3://catalog/bronze/catalog_rows", version=47)  # last good
df.write_delta("s3://catalog/bronze/catalog_rows", mode="overwrite")
```

Then re-run dbt silver/gold.

---

## Observability — where to look

| Signal           | Location                                          |
| ---------------- | ------------------------------------------------- |
| Logs             | `docker compose logs -f track-a`                  |
| Run status       | `ingest_runs` table                               |
| Per-row failures | `ingest_audit` table + DLQ                        |
| LLM cost         | `ingest_audit.cost_usd` aggregated by `run_id`    |
| Queue depth      | BullMQ admin UI on `http://localhost:3001` (dev)  |
| Metrics          | Prometheus scrape on `http://localhost:3000/metrics` |
| Traces           | OTel exporter; configurable via `OTEL_EXPORTER_OTLP_ENDPOINT` |
| Track B lineage  | Stdout OpenLineage events (Marquez deploy is out of scope for PoC) |

---

## Performance benchmarks (target SLOs)

| Operation                          | Target | Measured |
| ---------------------------------- | ------ | -------- |
| Track A: ingest 241 MB sample      | <8 min | TBD      |
| Track A: insert throughput (single conn) | >5000 rows/sec | TBD |
| Track A: fitment lookup query      | <50 ms | TBD      |
| Track A: peak RAM during ingest    | <300 MB | TBD     |
| Track B: bronze write throughput   | >10k rows/sec | TBD |
| Track B: dbt silver+gold full run  | <2 min | TBD      |

Benchmark scripts live in `track-a-jd-native/test/benchmark/` and `track-b-data-engineering/notebooks/bench.ipynb`.
