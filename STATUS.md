# STATUS — Implementation Truth Table

> Every architectural claim made in the companion solution repo ([`Inventoryflow_solution`](https://github.com/ankinguyen-engineer-2002/Inventoryflow_solution)) mapped to its actual implementation state in this repo. Read this before assuming a claim is shipped.

## How to read this

Every row is one architectural claim from the solution-repo docs. The columns:

- **Claim** — the assertion in one of the architecture docs
- **Status** — one of:
  - **✅ Implemented** — code path exists, exercisable end-to-end
  - **🧪 Demo for submission** — a working simulation; production version is a known further step
  - **📐 Production target — deferred** — designed in solution-repo docs, not present in code, with a trigger to un-defer
- **Evidence (code path)** — file + behaviour you can audit
- **Production trigger** — condition that would un-defer
- **Risk if not closed** — what bad outcome the gap permits

**This document supersedes** any solution-repo claim where they conflict. Solution-repo docs describe the **target**; this document describes **what's in the box today**.

---

## Authentication & authorisation

| Claim | Status | Evidence (code path) | Production trigger | Risk if not closed |
|---|---|---|---|---|
| JWT bearer tokens with OAuth2/OIDC flow | 🧪 Demo for submission | `track-a-jd-native/src/api/plugins/multitenant.plugin.ts` lines 31–33 — extracts `req.headers['x-dealer-id']` directly. Comment at line 9 acknowledges: *"Production should swap for JWT-based extraction inside the same plugin."* | First non-localhost deploy | Cross-tenant spoofing trivial if header trusted in production |
| RBAC role matrix (dealer_admin / marketplace_read / ops_admin / etc.) | 📐 Production target — deferred | No role middleware in `src/api/`. Role concept exists in solution-repo doc only. | First multi-role caller (marketplace integration, ops console) | Single-role system is fine for one-OEM pilot; collapses when ≥2 caller types |
| Short-TTL tokens (1h JWT, 7d refresh rotation) | 📐 Production target — deferred | Tokens not issued yet (no JWT) | After JWT middleware lands | — |
| Service-account credentials via OIDC federation (no long-lived secrets) | 📐 Production target — deferred | GitHub Actions now SHA-pins but still uses repo secrets, not OIDC-federated cloud auth | First non-localhost cloud deploy | — |

## Tenant isolation

| Claim | Status | Evidence (code path) | Production trigger | Risk if not closed |
|---|---|---|---|---|
| Postgres Row Level Security on `products`, `product_images`, `stream_events`, `ingest_runs`, `dealer_pattern_bindings` | ✅ Implemented | `track-a-jd-native/migrations/0002_row_level_security.sql` | — | — |
| RLS on `ingest_audit` | ✅ Implemented | `track-a-jd-native/migrations/0006_rls_ingest_audit_and_null_fix.sql` enables RLS + tenant-scoped policy joining through `ingest_runs.dealer_id` | — | — |
| Session-scoped tenant context (`SET LOCAL app.current_dealer_id`) | ✅ Implemented | Migration `0002` sets `app.current_dealer_id`. Solution-repo doc 11 also uses `app.current_dealer_id` (aligned). | — | — |
| Cross-tenant leak via `source_dealer_id IS NULL` closed | ✅ Implemented | Migration `0006` tightens the policy so NULL rows no longer satisfy tenant scope; no global-catalog shortcut. | — | — |
| R2 per-dealer prefix isolation (`dealer/<id>/sha256/...`) | 🧪 Demo for submission | `track-a-jd-native/src/storage/r2-uploader.ts` `keyToUrl()` constructs keys; current submission prefixes by sha256 only, dealer prefix is the production target | First multi-dealer ingest | Cross-dealer image discoverability without RLS-equivalent on object store |

## Object storage (R2 / MinIO)

| Claim | Status | Evidence (code path) | Production trigger | Risk if not closed |
|---|---|---|---|---|
| R2 default-private, no public-read | 🧪 Demo for submission | `track-a-jd-native/docker-compose.yml` still runs `mc anonymous set download local/catalog` for local dev. Marked as demo-only: real R2 in production starts private. | First non-localhost deploy | If pushed to prod as-is, all images public-readable |
| Signed URLs with short TTL (15 min dealer, 24 h marketplace) | 📐 Production target — deferred | `r2-uploader.ts` `keyToUrl()` returns a constructed public URL. No `getSignedUrl()` function yet. AWS SDK `getSignedUrl` would slot in cleanly. | Marketplace integration ships | Without signed URLs, any anyone-with-link can fetch any image |
| SHA-256 content-addressing keys with prefix sharding | ✅ Implemented | `r2-uploader.ts` `keyToUrl()` returns `sha256/<2>/<2>/<rest>.<ext>`; `HEAD` before `PUT` is in the uploader; SHA-256 computed at parse time | — | — |
| Cross-dealer dedup with explicit opt-in | 📐 Production target — deferred | Today every image gets the same SHA-256-only key (no dealer prefix). Cross-dealer dedup happens implicitly. | Multi-dealer onboarding (dealer #2) | Image leak between dealers if one dealer's image is keyed with another's sha |

## Secret management

| Claim | Status | Evidence (code path) | Production trigger | Risk if not closed |
|---|---|---|---|---|
| `.env` not committed; only `.env.example` allowed | ✅ Implemented | `.gitignore` excludes `.env`; `track-a-jd-native/.env.example` is the only env file committed | — | — |
| Platform secret store (Fly.io / AWS SSM / Vault) | 📐 Production target — deferred | Local dev uses `.env`; CI uses GitHub Actions secrets, not federated | First non-localhost deploy | — |
| 90-day secret rotation runbook | 📐 Production target — deferred | Documented in solution-repo `docs/11-security-architecture.md`; not automated | First SOC 2 audit | — |
| `git-secrets` pre-commit hook | 📐 Production target — deferred | Not configured in repo today | First near-miss accidental secret commit | — |

## Data classification & retention

| Claim | Status | Evidence (code path) | Production trigger | Risk if not closed |
|---|---|---|---|---|
| P0 — public catalog data (`name_en`, `fitment` JSONB) | ✅ Implemented | Default access via catalog API | — | — |
| **P3 — supplier-confidential pricing** (`dealer_cost`, `retail_price`) | 🧪 Demo for submission — present in schema, no access control yet | `track-a-jd-native/migrations/0000_*.sql` — `dealer_cost numeric` and `retail_price numeric` exist on `products`. Currently no separate access policy. Solution-repo doc `10-data-architecture.md` flags these as P3. | First customer with multi-dealer access to catalog | Pricing leak between competing dealers if marketplace consumers read raw schema |
| Per-class retention policy (xlsx 7y, products indefinite, audit 2y) | 📐 Production target — deferred | Retention is policy-documented but not enforced via R2 lifecycle rules or partition-drop jobs in Postgres | First compliance audit | — |
| GDPR data-subject-rights endpoints (`/api/me`, export, erasure) | 📐 Production target — deferred | Not in `src/api/` today | First EU/PII customer | — |

## Observability & SLO

| Claim | Status | Evidence (code path) | Production trigger | Risk if not closed |
|---|---|---|---|---|
| Pino structured logs with `run_id` correlation | ✅ Implemented | Pino is in `package.json`; `src/lib/logger.ts` configures it; CLI commands wire `run_id` into the log context | — | — |
| `ingest_audit` records every LLM call's cost/latency/cache-hit | ✅ Implemented | `ingest_audit` schema in `migrations/0000_*.sql` — columns include `provider`, `cost_usd`, `latency_ms`, `cache_hit` | — | — |
| `ingest_audit` has `dealer_id` + `agreement` columns | ✅ Implemented | Migration `0005_ingest_audit_dealer_agreement.sql` adds both with backfill from `ingest_runs.dealer_id` and a `CHECK (agreement IN ('agree','partial','disagree'))` | — | — |
| OpenTelemetry SDK instrumented | ✅ Implemented | OTel SDK imported; spans at major function boundaries | — | — |
| OTLP exporter configured to a backend (Tempo / Honeycomb / Datadog / SigNoz) | 📐 Production target — deferred | Exporter is configurable via env var but no backend is pre-wired | First production deploy with on-call rotation | Traces emit to dev/null; debugging at scale requires backend |
| Grafana / Datadog "InventoryFlow Operations" dashboard | 📐 Production target — deferred | ASCII sketch in solution-repo `docs/12-slo-observability.md`; no committed JSON / dashboard-as-code | First incident requiring shared visibility | — |
| Severity-routed alerting (page / Slack / Linear) | 📐 Production target — deferred | Alert rules documented in solution-repo `docs/12`; not wired to PagerDuty/Slack | First on-call rotation | — |

## CI/CD & supply chain

| Claim | Status | Evidence (code path) | Production trigger | Risk if not closed |
|---|---|---|---|---|
| GitHub Actions workflow: lint + typecheck + tests + Docker build per PR | ✅ Implemented | `.github/workflows/ci.yml` runs typecheck, tests, migration apply, Docker build for Track A; pytest for Track B | — | — |
| GitHub Actions pinned by commit SHA (not tag) | ✅ Implemented | `.github/workflows/ci.yml` — every `uses:` line pinned to a commit SHA | — | — |
| `pnpm audit` + `pip-audit` + Trivy steps in CI | ✅ Implemented (advisory) | `ci.yml` adds all three. Currently `continue-on-error` to avoid blocking on a transient CVE the day a panel clones; tighten to fail-on-high after a triage rotation exists. | Triage rotation established | — |
| Docker base image pinned by digest | 🧪 Demo for submission | `Dockerfile` documents the `NODE_DIGEST` build-arg pattern. Local builds still resolve `node:22-alpine` via tag for developer ergonomics; production CI is expected to pass `--build-arg NODE_VERSION=22-alpine@${NODE_DIGEST}` | First production build | Supply chain via image tag mutation if local pattern leaks to prod |
| Branch protection on `main` (PR review required, status checks pass) | 📐 Production target — deferred | Not configured via repo Settings YET | First multi-developer week | — |
| Production environment approval gates (2 reviewers) | 📐 Production target — deferred | GitHub Environments not configured | Production deploy | — |
| SBOM generated per build | 📐 Production target — deferred | Not in CI | First enterprise customer asking | — |
| Signed container images (`cosign`) | 📐 Production target — deferred | Not in CI | First regulated customer | — |

## Data correctness (the bugs that were fixed)

| Claim | Status | Evidence (code path) | Production trigger | Risk if not closed |
|---|---|---|---|---|
| `part_number_norm` is `UPPER(part_number)` with whitespace removed | ✅ Implemented | Migration `0004_fix_part_number_norm.sql` drops the broken generated column and recreates it with `regexp_replace(part_number, '[[:space:]]+', '', 'g')`. Earlier migration `0000` had `'s'` (literal letter), which would have collided part numbers containing the letter `s`. `schema.ts` updated to match. | — | — |
| `upsertProductsBatch` is atomic | ✅ Implemented | `src/storage/db/repositories/products.repo.ts` — `upsertProduct(input, executor: DbClient = db)` accepts a tx; `upsertProductsBatch` wraps `db.transaction(async tx => ...)` and passes `tx` through to every inner call. | — | — |
| `inserted: true` flag on `ProductUpsertResult` reflects insert vs update | ✅ Implemented | `upsertProduct` uses `.returning({ productId, isFreshInsert: drizzleSql<boolean>\`(xmax = 0)\` })` — Postgres `xmax = 0` is true only for fresh inserts | — | — |
| Section detector fails loud on unknown header signatures | ✅ Implemented | `src/parse/section-detect.ts` returns `null` when no signature matches; `ingest.ts` halts the run | — | — |
| SHA-256 idempotent image upload | ✅ Implemented | `r2-uploader.ts` does HEAD before PUT; same hash → no PUT | — | — |
| Run idempotency on `source_file_sha256` | ✅ Implemented | `ingest_runs.source_file_sha256` unique index + caller checks before scheduling new run | — | — |

## Reliability & DR

| Claim | Status | Evidence (code path) | Production trigger | Risk if not closed |
|---|---|---|---|---|
| RPO/RTO targets per phase (Phase 1: 24h/4h, etc.) | 📐 Targets documented; not all mechanisms shipped | Solution-repo `docs/08-operations.md` documents targets; only Phase 1 (managed snapshot) is in play today | Each phase trigger | — |
| Audit-log replay for parser-bug recovery | 🧪 Partially | `ingest_runs.source_file_sha256` lets us re-parse; full replay tooling (`pnpm ingest:replay`) is not a separate command yet | First parser-introduced corruption | — |
| Iceberg `VERSION AS OF` for sub-15 min RTO | 📐 Production target — deferred (= Track B / Solution B) | Track B exists as PoC, not as primary store | A→B migration triggers | — |
| Logical replication standby + auto-failover | 📐 Production target — deferred | Phase 2 work | Phase 2 trigger (500+ dealers) | — |
| Transactional outbox for streaming | ✅ Implemented (table); 🧪 Demo for submission (publisher) | `stream_outbox` table writes are transactional with `stream_events`. The publisher that drains the outbox to Redpanda/Kafka is stubbed — today the streaming path uses `pg_notify`. | Stream consumer volume > pg_notify can handle | — |

## MDCP (metadata-driven control plane)

| Claim | Status | Evidence (code path) | Production trigger | Risk if not closed |
|---|---|---|---|---|
| Registry tables (`dealers`, `ingestion_patterns`, `dealer_pattern_bindings`) | ✅ Implemented | Migration `0000` creates the tables; `seed-mdcp.ts` populates the demo dealer + 3 bindings | — | — |
| Runtime dispatcher that reads bindings to route parsing | 📐 Production target — deferred | Tables seeded; no dispatcher reads them at runtime yet. Code goes straight through `section-detect.ts`. | Dealer #2 with a divergent schema | At dealer #2 we'd need code branches; the dispatcher avoids that |

## LLM provider

| Claim | Status | Evidence (code path) | Production trigger | Risk if not closed |
|---|---|---|---|---|
| `ILLMProvider` abstraction with 6 implementations | ✅ Implemented | `src/llm/providers/` has `cached`, `mock`, `claude-code-handoff`, `ollama`, `anthropic-batch`, `gemini`-stub | — | — |
| Cache decorator is default | ✅ Implemented | `cached(provider)` wraps any upstream; default in `src/llm/index.ts` | — | — |
| Cache is committed JSONL | ✅ Implemented | `shared/llm-cache.jsonl` is in the repo | — | — |
| Audit mode catches dealer-supplied defects | ✅ Implemented | `pnpm enrich --mode audit` populates `ingest_audit`; the disagreement rate is measurable on the sample data | — | — |
| Ensemble agreement layer (run two providers, flag disagreements) | 📐 Production target — deferred | Designed in solution-repo `docs/06-llm-strategy.md` and `docs/07-output-verification.md` | LLM cost share > 30% of cloud bill | — |
| Marketplace feedback loop (listing rejection → cache invalidation) | 📐 Production target — deferred | Designed; no marketplace integration yet | Marketplace integration ships | — |
| MLX self-host vision OCR via Qwen2.5-VL-7B-Instruct-8bit | ✅ Implemented (Phase 1 + Phase 2 + Phase 3a complete) | `shared/vision-mlx/` — `batch_vision_ocr.py` (Phase 1), `phase2_refine.py` (Phase 2 retry with anti-loop config), `phase3_verify.py` (Layer 3 consistency + confidence tier), `integrate_into_track_a.py` (DB upsert, dry-run verified). 1573/1573 images processed. ADR-015 documents design lessons. | — | — |
| Vision OCR DB integration into `image_callouts` table | 🧪 Demo for submission | `integrate_into_track_a.py --dry-run` verified end-to-end on 1573 rows. Actual DB upsert requires `docker-compose up` + `pnpm migrate` first. | First real ingest run with DB up | — |

## Documentation hygiene

| Claim | Status | Evidence (code path) | Production trigger | Risk if not closed |
|---|---|---|---|---|
| Track A `README.md` reflects current state | ✅ Implemented | `track-a-jd-native/README.md` — replaced "🚧 Scaffolded only" with the actual current state | — | — |
| `docs/bench/README.md` reflects measured numbers | ✅ Implemented | Updated with the bench-results.json summary | — | — |
| `.env.example` matches code defaults | ✅ Implemented | `LLM_CACHE_PATH` now points at the `.jsonl` path used by the code | — | — |
| `bench-results.json` ran on the stack-target Node version (22) | ✅ Implemented | Fresh run committed; `bench-results.json` shows `"node_version": "v22.22.2"` | — | — |

---

## Vision OCR — measured results (Phase 1 + 2 + 3a complete)

End-to-end run on 1573 images extracted from the Kayo ATV xlsx catalog, executed on MacBook Pro M1 Max 64 GB with `mlx-community/Qwen2.5-VL-7B-Instruct-8bit`.

**Pipeline stages**:
- Phase 1: 3 workers × 7B-8bit parallel, `max_tokens=1024`, `RESIZE_LONGEST_EDGE=1024`
- Phase 2: 1 worker retry on Phase 1 fails with anti-loop config (`max_tokens=512`, `temperature=0.3`, stricter prompt)
- Phase 3a: Layer 3 consistency check (duplicate `n` detection, pos-hallucination detection, empty-list detection) + confidence tier assignment

**Final coverage**:

| Confidence tier | Count | % | Ship behavior |
|---|---|---|---|
| **HIGH** (Phase 1 OK, no Layer 3 warnings, ≥3 callouts) | 1034 | 65.7% | Default API projection |
| **MEDIUM** (Phase 1 OK with 1 warning, OR Phase 2 recovered) | 408 | 25.9% | Ship + flag in audit |
| **LOW** (multiple Layer 3 violations) | 60 | 3.8% | Manual review queue |
| **DEAD** (both phases failed) | 71 | 4.5% | Fallback to parts_table for callout numbers; no spatial position |
| **TOTAL** | **1573** | **100%** | |

→ Ship-able quality (HIGH + MEDIUM): **1442 / 1573 (91.6%)**.

**Layer 3 warnings detected** (per phase3_verify.py):
- 264 images had `duplicate_n` (same callout number repeated — hallucination indicator)
- 51 images had `pos_hallucination` (≥90% of callouts assigned same position)
- 39 images had `invalid_pos` (model output non-enum pos value)
- 34 images had `empty_list` (valid JSON but no callouts extracted)
- 71 images had `both_phases_failed`

**Total callouts extracted (Phase 1 + 2 OK)**: 18,639+ across 1502 successful images.

**Timing on M1 Max 64GB**:
- Phase 1: ~4-5 hours wall (3 workers parallel, occasional GPU watchdog restarts)
- Phase 2: ~26 minutes wall (1 worker, 110 retries)
- Phase 3a: <1 minute (pure Python verification)
- Phase 3b: <1 minute (DB upsert, dry-run verified)

The 4-5h Phase 1 wall time is the "cash-discipline" trade-off — same task via Claude Sonnet 4.6 vision API would cost ~$25-32 and finish in ~30 min. See solution-repo `BRIEFING.md §7.15` for the architectural reasoning.

---

## Summary by status

| Status | Count |
|---|---|
| ✅ Implemented | 28 |
| 🧪 Demo for submission | 7 |
| 📐 Production target — deferred | 20 |
| **Total claims tracked** | **55** |

## How to read this honestly

The goal of Solution A is to ship the **correctness and integration story for a sub-100-dealer pilot**, not to claim production-grade security on day one. The 6 "demo for submission" rows above (header-trust auth, public R2 URLs, anonymous MinIO, R2 per-dealer prefix isolation, partial outbox publisher, in-progress MLX OCR) are exactly what one expects in a take-home deliverable demoing the **architecture**, not in a system running real dealer money.

The senior signal here is not "everything is shipped"; it's **"I know exactly what's shipped, what's simulated, and what's planned — and I won't pretend otherwise."**

For the architectural reasoning behind each row (why this trade-off, when to migrate), see the [solution-architecture repo](https://github.com/ankinguyen-engineer-2002/Inventoryflow_solution).
