"""Integrate MLX vision OCR output into Track A's image_callouts table.

INPUT: shared/mlx-vision-output-final.jsonl
  (produced by phase3_verify.py — merged Phase 1 + Phase 2 + confidence tier)

This script:
  1. Reads the final merged JSONL with confidence already assigned
  2. Re-hashes each source image file to derive full 64-char SHA-256
     (Phase 1/2 records only the first 12 chars from filename)
  3. Looks up source_sheets via manifest.csv
  4. Upserts into `image_callouts` table with confidence tier from Phase 3a
  5. For DEAD records (both phases failed), inserts row with empty callouts
     and confidence='low' — downstream consumers fallback to parts_table

Use --dry-run to print what would be upserted without DB connection
(useful when Docker compose isn't up).

Schema:
    image_sha256       text PK
    callouts           jsonb
    callout_count      int
    confidence         text (high|medium|low)   # NOTE: schema only allows these 3
    vision_provider    text
    cache_hit          bool
    source_sheets      jsonb
    image_size_bytes   int
    extracted_at       timestamptz
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "shared"
# Prefer Phase 4 output (with precision-aware confidence) over Phase 3a
FINAL_INPUT_WITH_COVERAGE = SHARED_DIR / "mlx-vision-output-final-with-coverage.jsonl"
FINAL_INPUT_PHASE3 = SHARED_DIR / "mlx-vision-output-final.jsonl"
MANIFEST_CSV = Path(__file__).resolve().parent / "extracted_images" / "manifest.csv"


def get_input_file() -> Path:
    """Prefer Phase 4 (precision-aware) over Phase 3a if available."""
    if FINAL_INPUT_WITH_COVERAGE.exists():
        print(f"[integrate] using Phase 4 output (precision-aware confidence)")
        return FINAL_INPUT_WITH_COVERAGE
    if FINAL_INPUT_PHASE3.exists():
        print(f"[integrate] using Phase 3a output (Layer 3 only — run phase4_coverage.py for full)")
        return FINAL_INPUT_PHASE3
    return Path()


def load_sheet_mapping() -> dict[str, list[str]]:
    """sha256 → sorted list of distinct sheet names from manifest."""
    mapping: dict[str, set[str]] = defaultdict(set)
    if not MANIFEST_CSV.exists():
        print(f"[integrate] manifest not at {MANIFEST_CSV}")
        return {}
    with MANIFEST_CSV.open() as f:
        for row in csv.DictReader(f):
            mapping[row["sha256"]].add(row["sheet_name"])
    out = {sha: sorted(sheets) for sha, sheets in mapping.items()}
    print(f"[integrate] manifest mapping: {len(out)} unique sha256")
    return out


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def normalize_confidence(conf: str) -> str:
    """DB schema only allows high/medium/low. Map 'dead' → 'low'."""
    return "low" if conf == "dead" else conf


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would be upserted, don't connect to DB")
    args = ap.parse_args()

    final_input = get_input_file()
    if not final_input.exists():
        print(f"[integrate] ERROR: no input file found. Run phase3_verify.py first.")
        return 1

    sheet_mapping = load_sheet_mapping()

    records: list[dict] = []
    for line in final_input.read_text().splitlines():
        records.append(json.loads(line))
    print(f"[integrate] loaded {len(records)} records from {final_input.name}")

    # Build upsert rows
    rows_to_upsert: list[tuple] = []
    tier_counts = Counter()
    no_file = 0
    for rec in records:
        src_path = REPO_ROOT / rec["src_path"]
        if not src_path.exists():
            no_file += 1
            continue
        sha = hash_file(src_path)
        callouts = rec.get("ocr_result") or []
        callout_count = len(callouts) if isinstance(callouts, list) else 0
        confidence = normalize_confidence(rec.get("confidence", "low"))
        size_bytes = src_path.stat().st_size
        sheets = sheet_mapping.get(sha, [])
        provider = rec.get("vision_provider", "mlx-qwen2.5-vl-7b-instruct-8bit")
        tier_counts[rec.get("confidence", "low")] += 1

        rows_to_upsert.append((
            sha,
            json.dumps(callouts if isinstance(callouts, list) else []),
            callout_count,
            confidence,
            provider,
            False,  # cache_hit — local inference is not cached at HTTP level
            json.dumps(sheets),
            size_bytes,
        ))

    print(f"[integrate] prepared {len(rows_to_upsert)} rows for upsert")
    print(f"[integrate] missing source files: {no_file}")
    print(f"[integrate] confidence distribution:")
    for tier in ("high", "medium", "low", "dead"):
        print(f"[integrate]   {tier:8s}: {tier_counts[tier]}")
    print(f"[integrate]   (note: 'dead' rows mapped to confidence='low' in DB,"
          f" vision_provider='fallback-parts-table-only')")

    if args.dry_run:
        print(f"\n[integrate] DRY RUN — first 3 rows that would be upserted:")
        for r in rows_to_upsert[:3]:
            print(f"  sha={r[0][:12]}... count={r[2]} conf={r[3]} provider={r[4]}"
                  f" sheets={r[6][:60]}")
        print(f"\n[integrate] DRY RUN complete. Re-run without --dry-run to apply.")
        return 0

    # Real DB upsert path
    try:
        import psycopg  # noqa: PLC0415 — lazy import so --dry-run works without psycopg
    except ImportError:
        print("[integrate] ERROR: psycopg not installed. pip install psycopg[binary]")
        return 1

    db_url = os.environ.get("DATABASE_URL", "postgresql://dev:dev@localhost:5432/catalog")
    print(f"[integrate] DATABASE_URL={db_url}")

    upserts = 0
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        for r in rows_to_upsert:
            cur.execute(
                """
                INSERT INTO image_callouts (
                    image_sha256, callouts, callout_count, confidence,
                    vision_provider, cache_hit, source_sheets,
                    image_size_bytes, extracted_at
                )
                VALUES (%s, %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s, NOW())
                ON CONFLICT (image_sha256) DO UPDATE SET
                    callouts = EXCLUDED.callouts,
                    callout_count = EXCLUDED.callout_count,
                    confidence = EXCLUDED.confidence,
                    vision_provider = EXCLUDED.vision_provider,
                    cache_hit = EXCLUDED.cache_hit,
                    source_sheets = EXCLUDED.source_sheets,
                    image_size_bytes = EXCLUDED.image_size_bytes,
                    extracted_at = EXCLUDED.extracted_at;
                """,
                r,
            )
            upserts += 1
        conn.commit()

    print(f"\n[integrate] ============= SUMMARY =============")
    print(f"[integrate] image_callouts upserts: {upserts}")
    print(f"[integrate] missing source files:   {no_file}")
    print(f"[integrate] DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
