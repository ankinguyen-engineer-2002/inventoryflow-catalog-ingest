#!/usr/bin/env bash
# Daily vision-extraction rotation runner.
#
# Run this once a day to drain Groq's 1000-RPD free quota into the
# shared LLM cache. Each run picks up where the previous left off via
# SHA-256-keyed cache (no duplicate API calls).
#
# Usage:
#   ./scripts/vision_daily_run.sh                 # use defaults
#   ./scripts/vision_daily_run.sh --limit 500     # cap to 500 calls
#
# Prerequisites:
#   1. Edit this script and replace the GROQ_API_KEY placeholder, or
#      export GROQ_API_KEY in your shell before invoking.
#   2. Optionally export OPENROUTER_API_KEY for tier-1 OCR fallback.
#   3. Run from the track-b-data-engineering/ directory.

set -e

cd "$(dirname "$0")/.."

# ── API keys (override via environment) ─────────────────────────────
: "${GROQ_API_KEY:?Set GROQ_API_KEY in env before running this script}"
: "${OPENROUTER_API_KEY:=}"   # optional, leave empty if not using OpenRouter

# ── Runtime config ──────────────────────────────────────────────────
: "${CONCURRENCY:=2}"          # 2 = sweet spot, low burst risk
: "${MIN_INTERVAL_S:=5}"        # 12 req/min, safely under Groq 30k TPM
: "${VENV_PATH:=.venv-parity}"  # parity venv (Python 3.13 stack)

echo "=========================================="
echo "  Vision daily rotation — $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================="

# Activate venv
if [ ! -d "$VENV_PATH" ]; then
  echo "❌ venv not found at $VENV_PATH"
  echo "   Run: python3.13 -m venv $VENV_PATH && source $VENV_PATH/bin/activate && pip install polars openpyxl pyarrow pyiceberg[s3fs,duckdb]==0.8.* httpx duckdb"
  exit 1
fi
# shellcheck source=/dev/null
source "$VENV_PATH/bin/activate"

# Show current status
python3 scripts/vision_status.py

# Run extraction
echo "→ Starting Groq rotation, concurrency=$CONCURRENCY, min-interval=${MIN_INTERVAL_S}s"
echo "→ Will stop when Groq returns sustained 429 (daily quota exhausted)"
echo ""

python3 -u scripts/vision_extract_all.py \
  --provider groq \
  --concurrency "$CONCURRENCY" \
  --min-interval-s "$MIN_INTERVAL_S" \
  "$@"

echo ""
echo "=========================================="
echo "  Done at $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================="

# Show updated status
python3 scripts/vision_status.py
