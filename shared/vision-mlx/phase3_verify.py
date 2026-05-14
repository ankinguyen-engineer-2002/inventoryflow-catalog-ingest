"""Phase 3a — verify Phase 1+2 output and assign confidence tier.

Inputs:
  - shared/mlx-vision-output.{0,1,2}.jsonl   (Phase 1, 7B-8bit)
  - shared/mlx-vision-output-phase2.jsonl    (Phase 2 retry, anti-loop config)

Strategy:
  1. Merge Phase 1 + Phase 2 (Phase 2 OK overrides Phase 1 fail).
  2. Apply Layer 3 consistency checks per record:
     - Duplicate `n` in same image → hallucination indicator
     - All-same `pos` for ≥10 callouts → spatial-hallucination indicator
     - Callouts list not a list / empty → corrupted output
  3. Assign confidence tier:
       'high'   — Phase 1 OK, no Layer 3 violations, callout_count ≥ 3
       'medium' — Phase 2 OK, OR Phase 1 OK with 1 Layer 3 warning
       'low'    — multiple Layer 3 violations, OR very short callout list (<3)
       'dead'   — both phases failed → fallback to parts_table for callout numbers
  4. Output single merged file: shared/mlx-vision-output-final.jsonl

No ground-truth (parts_table) cross-reference yet — that's a runtime step
in integrate_into_track_a.py once DB is up. This script is intrinsic
verification only.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "shared"
OUTPUT_FILE = SHARED_DIR / "mlx-vision-output-final.jsonl"

VALID_POS = {
    "top-left", "top", "top-right",
    "center-left", "center", "center-right",
    "bottom-left", "bottom", "bottom-right",
}


def layer3_check(callouts: list) -> list[str]:
    """Return list of consistency warnings. Empty = clean."""
    warnings: list[str] = []
    if not isinstance(callouts, list):
        return ["not_a_list"]
    if not callouts:
        return ["empty_list"]

    # Duplicate `n`
    n_values = [c.get("n") for c in callouts if isinstance(c, dict)]
    n_counter = Counter(n_values)
    dups = [n for n, cnt in n_counter.items() if cnt > 1 and n is not None]
    if dups:
        warnings.append(f"duplicate_n:{len(dups)}")

    # All-same pos for ≥10 callouts
    pos_values = [c.get("pos") for c in callouts if isinstance(c, dict)]
    if len(pos_values) >= 10:
        most_common = Counter(pos_values).most_common(1)[0]
        if most_common[1] / len(pos_values) >= 0.9:
            warnings.append(f"pos_hallucination:{most_common[0]}@{most_common[1]}/{len(pos_values)}")

    # Invalid pos values
    invalid_pos = [p for p in pos_values if p not in VALID_POS and p is not None]
    if invalid_pos:
        warnings.append(f"invalid_pos:{len(invalid_pos)}")

    # n must be positive integer
    bad_n = [n for n in n_values if not isinstance(n, int) or n <= 0]
    if bad_n:
        warnings.append(f"bad_n_values:{len(bad_n)}")

    return warnings


def assign_confidence(phase1_ok: bool, phase2_ok: bool, warnings: list[str], callout_count: int) -> str:
    """Confidence tier per the 4-level scheme."""
    if not phase1_ok and not phase2_ok:
        return "dead"

    # Phase 2 recovered (Phase 1 failed, Phase 2 OK) → medium
    if phase2_ok and not phase1_ok:
        # Even Phase 2 OK with Layer 3 issues → low
        if len(warnings) >= 2:
            return "low"
        return "medium"

    # Phase 1 OK path
    if warnings:
        if len(warnings) >= 2 or any("hallucination" in w for w in warnings):
            return "low"
        return "medium"

    if callout_count < 3:
        # very short list — possibly correct (small image with few callouts) but suspicious
        return "medium"

    return "high"


def load_phase1() -> dict[str, dict]:
    """sha256_short → record (from per-worker jsonls)."""
    records: dict[str, dict] = {}
    for f in sorted(SHARED_DIR.glob("mlx-vision-output.[0-9].jsonl")):
        for line in f.read_text().splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            sha = d.get("sha256", "")
            if sha:
                records[sha] = d
    return records


def load_phase2() -> dict[str, dict]:
    """sha256_short → record (Phase 2 uses src_path basename, parse to short sha)."""
    records: dict[str, dict] = {}
    phase2_file = SHARED_DIR / "mlx-vision-output-phase2.jsonl"
    if not phase2_file.exists():
        return records
    for line in phase2_file.read_text().splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        # Derive short sha from src_path filename: NNN_<short>.png
        src = d.get("src_path", "")
        stem = Path(src).stem
        sha_short = stem.split("_", 1)[-1] if "_" in stem else stem
        records[sha_short] = d
    return records


def main() -> int:
    p1 = load_phase1()
    p2 = load_phase2()
    print(f"[phase3] Phase 1 records loaded: {len(p1)}")
    print(f"[phase3] Phase 2 records loaded: {len(p2)}")

    final: list[dict] = []
    tier_counts = Counter()
    warning_counts = Counter()
    src_p1_ok = src_p1_fail_p2_ok = src_p1_fail_p2_fail = 0

    for sha_short, p1_rec in p1.items():
        p1_ok = p1_rec.get("ocr_result") is not None
        p2_rec = p2.get(sha_short)
        p2_ok = p2_rec is not None and p2_rec.get("ocr_result") is not None

        # Source selection: prefer Phase 2 if it recovered, else Phase 1
        if p2_ok and not p1_ok:
            source_rec = p2_rec
            source_phase = 2
            src_p1_fail_p2_ok += 1
        elif p1_ok:
            source_rec = p1_rec
            source_phase = 1
            src_p1_ok += 1
        else:
            source_rec = p1_rec  # keep raw fail for record
            source_phase = 0
            src_p1_fail_p2_fail += 1

        callouts = source_rec.get("ocr_result") or []
        callout_count = len(callouts) if isinstance(callouts, list) else 0

        if source_phase == 0:
            warnings = ["both_phases_failed"]
        else:
            warnings = layer3_check(callouts)
        for w in warnings:
            warning_counts[w.split(":")[0]] += 1

        confidence = assign_confidence(p1_ok, p2_ok, warnings, callout_count)
        tier_counts[confidence] += 1

        final_rec = {
            "sha256": sha_short,
            "src_path": source_rec.get("src_path"),
            "ocr_result": callouts if source_phase > 0 else None,
            "callout_count": callout_count,
            "confidence": confidence,
            "warnings": warnings,
            "source_phase": source_phase,
            "vision_provider": (
                "mlx-qwen2.5-vl-7b-instruct-8bit"
                if source_phase > 0 else
                "fallback-parts-table-only"
            ),
            "latency_ms": source_rec.get("latency_ms", 0),
            "resized_to": source_rec.get("resized_to"),
            "error": source_rec.get("error") if source_phase == 0 else None,
        }
        final.append(final_rec)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w") as out:
        for rec in final:
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print()
    print(f"[phase3] ============= SUMMARY =============")
    print(f"[phase3] Total records: {len(final)}")
    print(f"[phase3]")
    print(f"[phase3] Source breakdown:")
    print(f"[phase3]   Phase 1 OK:          {src_p1_ok} ({100*src_p1_ok/len(final):.1f}%)")
    print(f"[phase3]   Phase 1 fail, P2 OK: {src_p1_fail_p2_ok} ({100*src_p1_fail_p2_ok/len(final):.1f}%)")
    print(f"[phase3]   Both phases fail:    {src_p1_fail_p2_fail} ({100*src_p1_fail_p2_fail/len(final):.1f}%)")
    print(f"[phase3]")
    print(f"[phase3] Confidence tiers:")
    for tier in ("high", "medium", "low", "dead"):
        n = tier_counts[tier]
        print(f"[phase3]   {tier:8s}: {n:>4} ({100*n/len(final):.1f}%)")
    print(f"[phase3]")
    print(f"[phase3] Layer 3 warnings detected:")
    for w, n in warning_counts.most_common():
        print(f"[phase3]   {w:30s}: {n}")
    print(f"[phase3]")
    print(f"[phase3] Output: {OUTPUT_FILE}")
    print(f"[phase3] DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
