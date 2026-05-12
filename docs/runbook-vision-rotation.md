# Vision LLM extraction — daily rotation runbook

This runbook describes the **operator** path for incrementally filling
the shared vision cache (`shared/llm-cache.jsonl`) by rotating through
free-tier API providers over multiple days.

Architecture and cost economics live in [`docs/decisions/ADR-007-llm-provider-cost-strategy.md`](decisions/ADR-007-llm-provider-cost-strategy.md) §Vision at scale.

## When to use this

You're running this manually only because:
- The first cold-start ingest hasn't filled the cache yet
- You're bound to free tiers for the submission demo
- Production deployment uses cron + Anthropic Batch (see ADR-007 v3 §Tier 4)

## Prerequisites

```bash
cd track-b-data-engineering/
ls .venv-parity/bin/python3   # should exist (Python 3.13)
echo $GROQ_API_KEY            # must be set (gsk_...)
echo $OPENROUTER_API_KEY      # optional, used as Tier-1 OCR fallback
```

If the venv is missing:
```bash
python3.13 -m venv .venv-parity
source .venv-parity/bin/activate
pip install polars openpyxl pyarrow 'pyiceberg[s3fs,duckdb]==0.8.*' httpx duckdb
```

## Daily routine

### Step 1 — Check status

```bash
cd track-b-data-engineering
source .venv-parity/bin/activate
python3 scripts/vision_status.py
```

Sample output:
```
  Total unique images processed : 230 / 1586 (14.5%)
  Remaining to process          : 1356
  Images with real callouts     : 215 (93.5% of processed)

  By provider:
    groq-vision                      125
    ollama-vision                     81
    openrouter-vision                 24

  → Estimated days to 100% @ Groq 1k RPD: 2
```

### Step 2 — Run rotation

```bash
./scripts/vision_daily_run.sh
```

Defaults:
- Provider: Groq (Llama-4 Scout 17B)
- Concurrency: 2
- Min interval: 5s (12 req/min, safely under Groq 30k TPM)
- Stops automatically when Groq returns sustained 429 (RPD exhausted)

What it does:
1. Activates `.venv-parity`
2. Prints pre-run status
3. Calls `vision_extract_all.py --provider groq --concurrency 2 --min-interval-s 5`
4. Each call reads cache first → cache hit = instant skip (no API)
5. Cache miss → call Groq → write result → cache miss next time becomes hit
6. When Groq RPD exhausts (≈1000 calls in), starts getting 429 → script eventually stops
7. Prints post-run status

**Typical session: ~60-90 minutes for 700-1000 fresh entries.**

### Step 3 — Commit progress (optional, daily)

```bash
cd ../
git add shared/llm-cache.jsonl sample-output/vision-extracted-callouts.json
git commit -m "chore(vision): daily Groq rotation +N entries → M/1586"
git push origin main
```

### Step 4 — At 100% coverage, materialise into both tracks

```bash
# Track A — populate image_callouts PG table
cd ../track-a-jd-native
DATABASE_URL=postgres://dev:dev@localhost:5432/catalog pnpm extract-callouts

# Track B — populate silver_image_callouts Iceberg table
cd ../track-b-data-engineering
source .venv-parity/bin/activate
python3 scripts/materialize_all.py
```

Both commands are idempotent and replay-safe.

## Automation (optional)

Add to crontab for fully-automated daily run:
```bash
crontab -e
```
```cron
# Run vision rotation every day at 08:00
0 8 * * * cd $HOME/path/to/repo/track-b-data-engineering && GROQ_API_KEY=gsk_... ./scripts/vision_daily_run.sh >> /tmp/vision-cron.log 2>&1
```

## Troubleshooting

### Groq returns 429 immediately on first call
Daily quota already exhausted from earlier session. Check `x-ratelimit-reset-requests` header — wait until reset.

### Script never finishes
The retry-on-429 logic caps retries at 5 attempts × 30s sleep. If Groq is fully exhausted, all 5 retries fail and the script falls through to "Null result" (cache_hit=False). The next session picks up where this left off.

### Cache file has duplicate SHA entries
Should not happen — `cached.py` uses `dict.setdefault` and `_compute_key` is deterministic over `(field, image_sha256)`. Verify with `scripts/vision_status.py` — it dedupes by SHA.

### "OpenRouter 429 — sleeping 30s"
OpenRouter's free tier on `:free` models is 50 RPD per account. Already exhausted today. Either:
- Top up $10 to lift to 1000 RPD
- Or rely on Tier 2 (Groq) only — set `OPENROUTER_API_KEY=` (empty) before running

## Why this approach vs paying for completion

See ADR-007 v3 §Vision at scale for the full reasoning. Short version:
- $0.40 to finish 1586 today vs $0 over 2 days both work
- The **architecture** (5-tier fallback + SHA-256 dedup + quota rotation) is the deliverable
- Coverage at 14.5% (architecture-complete) or 100% (cache-complete) — same code, same docs, same Iceberg/PG tables
- Free-tier rotation is what the production system would do anyway at 10k files/week — see ADR-007 v3 math
