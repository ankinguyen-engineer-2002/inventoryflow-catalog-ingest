# Execution Plan — 5-day step-by-step

> Day-by-day, AM/PM, step-by-step actions with deliverables, test criteria, and checkpoints.
>
> Companion to [`PLAN.md`](../PLAN.md) (the architectural plan).

---

## Pre-flight checklist (Day 0)

Run once before Day 1.

- [ ] Docker Desktop installed (`docker --version` → ≥24.x)
- [ ] Node.js 22 LTS via `nvm` or `fnm` (`node -v` → v22.x)
- [ ] pnpm 9 (`npm i -g pnpm@9`)
- [ ] `psql` CLI (Postgres client) — `brew install libpq && brew link --force libpq`
- [ ] `mc` CLI (MinIO client) — `brew install minio/stable/mc`
- [ ] (Optional Day 4+) Python 3.12 + Poetry — `brew install python@3.12 poetry`
- [ ] Sample data placed at `shared/sample-data/example.xlsx` (the 241 MB Talemy file)
- [ ] Git config local set: `Aric Nguyen` / `aricnguyen.analytics2002@gmail.com`

---

## TRACK A — TypeScript JD-Native (Days 1–3)

### Day 1 — Foundation + ingest core

**Goal**: Parse 1 sheet end-to-end (no DB write yet). Validate the algorithm.

#### Day 1 AM (≈4 h)

| Step | Task                                                                    | Deliverable                                  | Test                                            |
| ---- | ----------------------------------------------------------------------- | -------------------------------------------- | ----------------------------------------------- |
| 1.1  | Create `track-a-jd-native/package.json` with all deps pinned             | File exists, `pnpm install` succeeds         | `pnpm install` → no errors                       |
| 1.2  | Create `tsconfig.json` strict mode + `.nvmrc` + `.npmrc`                 | Files exist                                  | `pnpm tsc --noEmit` runs                         |
| 1.3  | Create `docker-compose.yml` (PG 16 + Redis 7 + MinIO) with healthchecks  | Compose file                                 | `docker compose config` validates               |
| 1.4  | Create `drizzle.config.ts`                                               | File exists                                  | `pnpm drizzle-kit --help` works                  |
| 1.5  | Create `src/storage/db/schema.ts` — full 11-table Drizzle schema         | Schema file                                  | `pnpm tsc --noEmit` clean                       |
| 1.6  | Generate first migration `pnpm drizzle-kit generate`                     | `migrations/0001_init.sql`                   | File exists, contains all CREATE TABLEs          |
| 1.7  | Create `src/lib/env.ts` (Zod env validation)                             | env loader                                   | Unit test: invalid env throws                    |
| 1.8  | Create `src/lib/logger.ts` (Pino structured)                             | Logger module                                | Logs JSON to stdout                              |
| 1.9  | Create `src/storage/db/client.ts` (PG pool via `postgres` driver)        | DB client                                    | Connects to local PG                             |
| 1.10 | Boot Docker: `docker compose up -d`                                      | 3 containers green                           | `docker compose ps` shows healthy                |
| 1.11 | Run migration: `pnpm db:migrate`                                         | Tables created                               | `psql -c '\dt'` shows 11 tables                  |

**Checkpoint M1A** — At end of AM, you should be able to run `docker compose up -d && pnpm db:migrate && psql -c '\dt'` and see all 11 tables. Commit this state.

#### Day 1 PM (≈4 h)

| Step | Task                                                                    | Deliverable                                  | Test                                             |
| ---- | ----------------------------------------------------------------------- | -------------------------------------------- | ------------------------------------------------ |
| 1.12 | Create `src/ingest/xlsx-reader.ts` — exceljs streaming wrapper           | Async iterator over rows + sheet meta        | Reads sample xlsx without loading full file      |
| 1.13 | Create `src/ingest/drawing-parser.ts` — parse `xl/drawings/*.xml`        | Map of (sheet_idx → image[]) with row anchors | Unit test on sample xml string                   |
| 1.14 | Create `src/ingest/section-detector.ts` — header-regex section detection| Sections array with header_row, data_rows, image_ref | Unit test 3 cases (chassis, engine, exception) |
| 1.15 | Create `src/ingest/fitment-resolver.ts` — sheet name → fitment           | Fitment object {year_start, year_end, model_code, variant} | Unit test 5 sheet names         |
| 1.16 | Create `src/cli/ingest.ts` (minimal CLI with `--dry-run` flag)           | Executable CLI                               | `pnpm ingest:dryrun <file> --sheet "FOXStorm 70 AY70-2 "` outputs JSON |
| 1.17 | Create `vitest.config.ts` + 6+ unit tests                                | Tests pass                                   | `pnpm test` → all green                          |
| 1.18 | Commit: `feat(ingest): xlsx streaming + section detection (M1)`         | Commit clean                                 | `git log -1` shows commit, no Co-Authored-By     |
| 1.19 | Push to GitHub                                                          | Remote updated                               | `gh repo view --web` shows latest commit         |

**Checkpoint M1** — `pnpm ingest:dryrun ../shared/sample-data/example.xlsx --sheet "FOXStorm 70 AY70-2 "` prints a JSON object with:
- 15+ sections detected
- Each section has `header_row_idx`, `data_row_count`, `image_anchor_row`, `image_filename`
- Sample 5 data rows printed

---

### Day 2 — Persistence + R2 + Workers

**Goal**: Full file (110 sheets) → Postgres + R2. Idempotent re-run.

#### Day 2 AM (≈4 h)

| Step | Task                                                                    | Deliverable                                  | Test                                              |
| ---- | ----------------------------------------------------------------------- | -------------------------------------------- | ------------------------------------------------- |
| 2.1  | Create `src/ingest/row-normalizer.ts` — Zod schema for product row       | Validated row type                           | Unit test: bad row rejected                       |
| 2.2  | Create `src/storage/db/repositories/products.repo.ts`                    | `upsertProducts(rows)` function              | Integration test with Testcontainers               |
| 2.3  | Create `src/storage/db/repositories/runs.repo.ts`                        | Run lifecycle (create, update status, stats) | Integration test                                  |
| 2.4  | Create `src/storage/r2-uploader.ts` — SHA-256 keyed, HEAD-skip            | `uploadImage(buf, ext) → r2_key`             | Integration test against MinIO                    |
| 2.5  | Update `cli/ingest.ts` — wire reader → normalizer → repo + uploader       | End-to-end on 10 sheets                      | Run on 10 sheets → DB has products + images       |
| 2.6  | Run on full file (110 sheets) — measure wall-time + RAM                  | Console log timing                           | <8 min, <300 MB peak                              |

#### Day 2 PM (≈4 h)

| Step | Task                                                                    | Deliverable                                  | Test                                              |
| ---- | ----------------------------------------------------------------------- | -------------------------------------------- | ------------------------------------------------- |
| 2.7  | Create `src/queue/queues.ts` + BullMQ queue definitions                  | Queue exports                                | Queues register without error                     |
| 2.8  | Create `src/queue/workers/parse-file.worker.ts` — fan-out 110 sheet jobs | Worker boots, processes 1 file → 110 jobs    | E2E: queue 1 file → workers process all          |
| 2.9  | Create `src/queue/workers/parse-sheet.worker.ts` — per-sheet logic        | Refactor of CLI logic into worker            | Same output as CLI                                |
| 2.10 | Create `src/queue/workers/upload-image.worker.ts` (conc=16, p-limit)     | Image upload async                            | All 1586 images in R2 after run                   |
| 2.11 | Create `src/queue/workers/dlq-replay.worker.ts`                          | Failed jobs in DLQ retryable                  | Inject failure → DLQ has job                      |
| 2.12 | Benchmark: insert throughput (`test/benchmark/insert.bench.ts`)          | Bench report                                  | >5000 rows/sec sustained                          |
| 2.13 | Idempotency test: re-run full ingest                                    | No duplicate products                         | `SELECT COUNT(*)` unchanged                       |
| 2.14 | Commit: `feat(queue,storage): BullMQ workers + R2 + idempotent (M2)`    | Clean commit                                  |                                                  |
| 2.15 | Push                                                                    |                                              |                                                  |

**Checkpoint M2** — `pnpm ingest ../shared/sample-data/example.xlsx`:
- Status `SUCCESS` in `ingest_runs`
- 12k+ products in DB
- 1586+ images in MinIO (deduped if SHA-256 match)
- 2nd run = idempotent (same row counts)
- Bench: insert >5000 rows/sec

---

### Day 3 — LLM + Streaming + Polish

**Goal**: Track A complete with batch + streaming + AI tooling.

#### Day 3 AM (≈4 h)

| Step | Task                                                                    | Deliverable                                  | Test                                              |
| ---- | ----------------------------------------------------------------------- | -------------------------------------------- | ------------------------------------------------- |
| 3.1  | Create `src/ai/provider.ts` — `ILLMProvider` interface                   | Interface + types                            | Compiles                                          |
| 3.2  | Create `src/ai/providers/mock.provider.ts`                              | Returns fixtures                             | Unit test                                         |
| 3.3  | Create `src/ai/providers/cached.provider.ts` — SQLite decorator         | Wraps any upstream                           | Cache hit on 2nd call                             |
| 3.4  | Create `src/ai/providers/claude-code-handoff.provider.ts`               | Emits + reads tasks/results JSON              | Unit test                                         |
| 3.5  | Create `src/ai/providers/ollama.provider.ts`                            | HTTP to Ollama                                | Skip if Ollama not running; mark skipped         |
| 3.6  | Create `src/ai/audit.ts` — log every call to `ingest_audit`              | Audit rows inserted                           | Integration test                                  |
| 3.7  | Create `src/queue/workers/enrich-llm.worker.ts` — translate CN→EN missing| Worker integrated with cache decorator       | Re-run ingest → enriches null name_en fields     |
| 3.8  | Generate LLM cache via `claude-code-handoff`                            | `shared/llm-cache.sqlite` populated          | Cache file 5-10 MB                                |
| 3.9  | Re-run ingest with `LLM_PROVIDER=cached` → verify $0 calls               | Audit log shows all cache_hit=true            | `SELECT cache_hit, COUNT(*) FROM ingest_audit`   |

#### Day 3 PM (≈4 h) — Streaming layer

| Step | Task                                                                    | Deliverable                                  | Test                                              |
| ---- | ----------------------------------------------------------------------- | -------------------------------------------- | ------------------------------------------------- |
| 3.10 | Create `src/api/server.ts` — Fastify app + health routes                 | App boots on PORT 3000                        | `curl /healthz` → 200                            |
| 3.11 | Create `src/api/routes/runs.routes.ts` — POST /runs, GET /runs/:id      | Run via HTTP                                  | curl POST → returns run_id                       |
| 3.12 | Create `src/api/routes/events.routes.ts` — POST /events/{inventory,pricing,order} | Webhook endpoints              | curl with body → 202 Accepted                    |
| 3.13 | Create `src/streaming/webhook-router.ts` — dispatch to stream queue      | Validated event enqueued                      | Unit test                                         |
| 3.14 | Create `src/streaming/pg-notify-publisher.ts` + `pg-listen-subscriber.ts`| NOTIFY/LISTEN flow                            | Integration: event → NOTIFY → consumer sees it   |
| 3.15 | Create `src/queue/workers/stream-inventory.worker.ts`                   | Worker upserts + NOTIFYs                      | E2E: webhook → DB updated → NOTIFY fires <500ms |
| 3.16 | Create `src/api/plugins/multitenant.plugin.ts` — dealer_id resolver     | Plugin attaches dealer_id to req              | Unit test JWT extraction                          |
| 3.17 | Add RLS policies to migrations + verify policies enforce                | Cross-tenant query returns 0 rows             | Integration test                                  |
| 3.18 | Update `docs/runbook.md` with Day 3 ops procedures                       | Doc updated                                   |                                                  |
| 3.19 | Commit: `feat(streaming,ai): webhooks + LLM cache + RLS (M3)`           | Clean commit                                  |                                                  |
| 3.20 | Push                                                                    |                                              |                                                  |

**Checkpoint M3** — Track A complete:
- Batch: file ingestion via `pnpm ingest` or `POST /runs`
- Streaming: `curl -X POST /events/inventory -d '{...}'` → DB updated <500 ms p95
- LLM cache committed, $0 reviewer experience
- RLS enforced, multi-tenant ready
- All unit + integration tests pass

---

## TRACK B — Modern OSS DE (Days 4–5)

### Day 4 — Dagster + Iceberg + dbt batch path

**Goal**: Same xlsx → Iceberg bronze → silver (Polars) → gold (dbt) on MinIO.

#### Day 4 AM (≈4 h)

| Step | Task                                                                    | Deliverable                                  | Test                                              |
| ---- | ----------------------------------------------------------------------- | -------------------------------------------- | ------------------------------------------------- |
| 4.1  | Create `track-b-data-engineering/pyproject.toml` + `poetry.lock`         | Deps installable                              | `poetry install` succeeds                         |
| 4.2  | Create `docker-compose.yml` (MinIO + PG + Dagster webserver + Iceberg REST + Redpanda + RisingWave + Kafka-Connect) | 7 containers green | `docker compose ps` healthy        |
| 4.3  | Create `dagster_project/resources.py` — IcebergIO, MinIO, Polars, dbt    | Resources wired                               | `dagster dev` boots                              |
| 4.4  | Create `dagster_project/assets/bronze.py` — Polars → Iceberg write       | Asset materializes                            | `dagster asset materialize` writes Iceberg table |
| 4.5  | Define Iceberg schema for `bronze.catalog_rows` partition spec           | Schema migration applied                      | `mc ls` shows partitioned files                   |
| 4.6  | Verify: parse xlsx + write bronze with row counts matching Track A       | Counts match                                  | Diff Track A counts vs Track B bronze counts: 0  |

#### Day 4 PM (≈4 h)

| Step | Task                                                                    | Deliverable                                  | Test                                              |
| ---- | ----------------------------------------------------------------------- | -------------------------------------------- | ------------------------------------------------- |
| 4.7  | Create `dagster_project/assets/silver.py` — 3 silver assets (parts/fitment/images) | Silver Iceberg tables          | Row counts match expected                         |
| 4.8  | Create `dagster_project/asset_checks.py` — 4 checks                      | Checks pass on good data                      | Inject bad row → check fails materialization     |
| 4.9  | Create `dbt_project.yml` + `dbt/models/gold/products_mart.sql`           | dbt models materialize                        | `dbt run` succeeds                               |
| 4.10 | Create `dbt/models/gold/catalog_marketplace_view.sql`                    | 2 gold marts on Iceberg                       | Row counts visible in Dagster UI                  |
| 4.11 | Create `dagster_project/sensors.py` — on_new_xlsx sensor                | Sensor triggers materialization               | Drop file in raw → sensor fires                  |
| 4.12 | Create `notebooks/duckdb_demo.ipynb` — 5 sample queries on Iceberg gold | Notebook runs                                 | Outputs match Track A row counts                  |
| 4.13 | Commit: `feat(track-b): Dagster + Iceberg batch path (M4)`              | Clean commit                                  |                                                  |
| 4.14 | Push                                                                    |                                              |                                                  |

**Checkpoint M4** — Track B batch path runnable:
- `make track-b-batch` → bronze + silver + gold tables exist in MinIO
- Dagster UI shows asset graph with column-level lineage
- DuckDB query parity with Track A's Postgres `products`

---

### Day 5 — Streaming + ADRs + Final polish

**Goal**: Track B streaming + ADR updates + README final + comparison numbers + submission.

#### Day 5 AM (≈4 h)

| Step | Task                                                                    | Deliverable                                  | Test                                              |
| ---- | ----------------------------------------------------------------------- | -------------------------------------------- | ------------------------------------------------- |
| 5.1  | Create `streaming/risingwave_views.sql` — CREATE SOURCE + MV + SINK     | Streaming SQL defined                         | RisingWave executes without error                 |
| 5.2  | Create `streaming/redpanda_seed.py` — sample event publisher            | Publishes inventory events                    | Topic has messages                                |
| 5.3  | Wire RisingWave → Iceberg sink                                          | `gold.live_inventory_view` table updates      | Publish event → row appears <1 s                 |
| 5.4  | Create `streaming/connect/debezium-postgres.json` — CDC config          | Kafka Connect picks up PG changes             | UPDATE PG → CDC event in Redpanda                |
| 5.5  | Measure streaming SLA: webhook → Iceberg MV refresh                     | Latency report                                | p95 <2 s                                          |

#### Day 5 PM (≈4 h)

| Step | Task                                                                    | Deliverable                                  | Test                                              |
| ---- | ----------------------------------------------------------------------- | -------------------------------------------- | ------------------------------------------------- |
| 5.6  | Fill measured numbers into `docs/COMPARISON.md` (replace `[estimated]`)| Real bench numbers                            | All `[measured]` tags resolved                    |
| 5.7  | Final README pass — fix any rendering issues on GitHub                  | Polish                                        | Visual inspection passes                          |
| 5.8  | Final run-through: clone repo to clean dir, follow README, time it      | <15 min for reviewer                          | All scripts work first-try                        |
| 5.9  | Update `docs/runbook.md` with final ops procedures                       | Doc complete                                  |                                                  |
| 5.10 | Final `git status` audit: nothing leaked (secrets, large files)         | Clean                                         | `git ls-files | xargs du -ah | sort -h | tail`   |
| 5.11 | Commit: `chore(release): submission ready (M5)`                        | Tagged commit                                 |                                                  |
| 5.12 | Push to GitHub                                                          | Final remote state                            | `gh repo view --web`                              |
| 5.13 | Tag: `v1.0.0-submission` + create GitHub release                       | Release published                             | URL ready to send recruiter                       |

**Checkpoint M5 — SUBMISSION READY** — every checkbox green:
- [ ] Track A: `docker compose up && pnpm install && pnpm db:migrate && pnpm ingest` runs end-to-end in <8 min
- [ ] Track B: `cd track-b && make track-b-up && make track-b-batch && make track-b-stream` runs in <10 min
- [ ] Both tracks: zero API key required, zero $ spent
- [ ] Sample query in README returns expected row from each track
- [ ] All 14 ADRs cross-link correctly on GitHub
- [ ] README renders cleanly (mermaid + badges + tables)
- [ ] `git log --format='%ae'` shows only `aricnguyen.analytics2002@gmail.com`
- [ ] Repo URL ready to send

---

## Bug-fix cycle conventions

Every step ends with **green tests + clean lint + manual smoke test** before moving to the next. If any of these fail:

1. **Reproduce minimally** — extract failing case to a unit test first.
2. **Diagnose root cause** — don't paper over symptoms. Read error trace fully.
3. **Fix the cause** — not the symptom.
4. **Add regression test** — the unit test from step 1 stays in the suite.
5. **Verify all previously-green tests still pass** — `pnpm test` full run.
6. **Commit with clear message** — `fix(<scope>): <what was broken and why>`.

If stuck >30 min on a bug:
- Write a comment in code with `// STUCK: <hypothesis>` and move on.
- Come back at end of day with fresh eyes.
- If still stuck after 1 h: escalate to runbook / TODO note for next day.

---

## Commit-message conventions (recap)

```
<type>(<scope>): <short summary>

<body — 2–4 paragraphs>
Problem: what was wrong / what's needed
Diagnosis: why current state is insufficient
Fix: what this commit changes
Trade-off: what we accept in return
```

Types: `feat`, `fix`, `refactor`, `chore`, `docs`, `test`, `perf`.

**NO `Co-Authored-By: Claude` trailer.** Sole author = Aric.

---

## Quick reference — common commands

```bash
# Track A
cd track-a-jd-native
docker compose up -d              # boot PG + Redis + MinIO
docker compose ps                 # check health
docker compose logs -f postgres   # tail logs
pnpm install                      # deps
pnpm db:migrate                   # apply migrations
pnpm db:studio                    # open Drizzle Studio UI
pnpm ingest <file>                # full ingest
pnpm ingest:dryrun <file>         # parse only, no DB write
pnpm test                         # all unit + integration tests
pnpm test:unit                    # just unit
pnpm test:integration             # just integration (needs PG up)
pnpm bench                        # benchmarks
pnpm lint                         # lint check
pnpm typecheck                    # tsc --noEmit

# Track B
cd track-b-data-engineering
docker compose up -d              # boot 7-container stack
poetry install
make track-b-batch                # bronze → silver → gold
make track-b-stream               # streaming MV + Iceberg sink
make track-b-query                # DuckDB queries on Iceberg gold
poetry run dagster dev            # local Dagster UI on :3000
poetry run dbt run                # run dbt models
poetry run pytest                 # tests
```

---

**Status updates**: append daily status to `CHANGELOG.md` under `[Unreleased]`.
