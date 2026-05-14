"""Phase 2 refinement — re-run 7B on Phase 1 failures with anti-loop config.

Phase 1 already used 7B-8bit. Failures are predominantly hallucination loops
(model repeats output until hitting max_tokens, JSON breaks). Same model+prompt
won't help. This script changes THREE things vs Phase 1:

  1. Stricter prompt: explicit "STOP after listing visible callouts, DO NOT
     REPEAT or extrapolate. If unsure, output less."
  2. Lower max_tokens=512: forces model to stop generation early, prevents
     the loop from eating GPU time to no useful output.
  3. Temperature=0.3: tiny randomness breaks deterministic loop patterns
     (temperature=0.0 means same input → same loop every time).

Inputs:
  - Phase 1 fails (110 records with ocr_result=None in mlx-vision-output.*.jsonl)
  - Slice-orphans (images with no entry in any Phase 1 output) — usually 0
    after a full Phase 1 run.

Output: mlx-vision-output-phase2.jsonl (or .{idx}.jsonl if --slice used)
"""
from __future__ import annotations

import argparse
import json
import resource
import time
from pathlib import Path

# Override model BEFORE importing parser (default in parser.py is 2B)
import parser as parser_module
parser_module.MODEL_ID = "mlx-community/Qwen2.5-VL-7B-Instruct-8bit"

from parser import ParserConfig, VisionParser  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "shared"
IMAGE_DIR = Path(__file__).resolve().parent / "extracted_images"
OUTPUT_FILE = SHARED_DIR / "mlx-vision-output-phase2.jsonl"

# Stricter prompt for Phase 2 — explicit anti-loop instructions
PHASE2_USER_PROMPT = (
    "Extract numbered callouts visible in this schematic.\n"
    "RULES:\n"
    "  - Output ONE JSON array, then STOP.\n"
    "  - DO NOT repeat the same callout number twice.\n"
    "  - DO NOT extrapolate callouts you can't clearly see.\n"
    "  - If you've already listed every visible callout, STOP — do NOT continue.\n"
    "  - Maximum 50 items per response. If schematic has more, list the clearest 50.\n"
    "Each item has exactly these keys:\n"
    '  "n" — integer, the callout number\n'
    '  "pos" — string, one of: top-left, top, top-right, center-left, '
    "center, center-right, bottom-left, bottom, bottom-right\n"
    'Example: [{"n": 1, "pos": "top-left"}, {"n": 2, "pos": "center"}]\n'
    "Output JSON only. No prose, no commentary, no repetition."
)


def rss_gb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if rss > 1024 * 1024 * 1024 * 1024:
        rss = rss // 1024
    return rss / 1024 / 1024 / 1024


def collect_all_images() -> set[Path]:
    images: set[Path] = set()
    for ext in ("*.png", "*.jpg", "*.jpeg"):
        for p in IMAGE_DIR.rglob(ext):
            if ".resized" not in p.name:
                images.add(p)
    return images


def collect_phase1_state() -> tuple[set[Path], set[Path]]:
    """Return (processed_ok, processed_fail) sets of image paths."""
    ok: set[Path] = set()
    fail: set[Path] = set()
    for f in sorted(SHARED_DIR.glob("mlx-vision-output.[0-9].jsonl")):
        for line in f.read_text().splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            src = REPO_ROOT / d["src_path"]
            if d.get("ocr_result") is None:
                fail.add(src)
            else:
                ok.add(src)
    return ok, fail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--slice",
        type=str,
        default=None,
        help='N/M for parallel workers (e.g. "0/3" for worker 0 of 3)',
    )
    args = ap.parse_args()
    idx, total = (0, 1)
    if args.slice:
        idx, total = (int(x) for x in args.slice.split("/"))

    all_images = collect_all_images()
    ok, fail = collect_phase1_state()
    missing = all_images - ok - fail
    todo_all = sorted(fail | missing)
    todo = [p for i, p in enumerate(todo_all) if i % total == idx] if total > 1 else todo_all

    print(f"[phase2] Phase 1 state:")
    print(f"[phase2]   ok (parsed) : {len(ok)}")
    print(f"[phase2]   parse_fail  : {len(fail)}")
    print(f"[phase2]   missing     : {len(missing)} (slice-orphans from dead workers)")
    print(f"[phase2] Total to refine in this slice: {len(todo)} / {len(todo_all)}")
    print(f"[phase2] Worker {idx}/{total}")

    if not todo:
        print("[phase2] nothing to do")
        return 0

    output_file = (
        SHARED_DIR / f"mlx-vision-output-phase2.{idx}.jsonl"
        if total > 1
        else SHARED_DIR / "mlx-vision-output-phase2.jsonl"
    )
    print(f"[phase2] writing to {output_file}")
    print(f"[phase2] using model: {parser_module.MODEL_ID}")

    cfg = ParserConfig(
        model_id=parser_module.MODEL_ID,
        max_tokens=512,      # Force early stop — Phase 1 fails were loop-to-cap
        temperature=0.3,     # Break deterministic loop patterns
    )
    p = VisionParser(cfg)
    t_load = time.perf_counter()
    p.load()
    print(f"[phase2] 7B model loaded in {time.perf_counter() - t_load:.1f}s, RAM={rss_gb():.2f} GB")

    ok_count = 0
    fail_count = 0
    t_start = time.perf_counter()
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w") as out:
        for i, img in enumerate(todo, 1):
            try:
                r = p.parse(img, user_prompt=PHASE2_USER_PROMPT)
                rec = {
                    "src_path": str(img.relative_to(REPO_ROOT)),
                    "ocr_result": r.parsed,
                    "raw_output": r.raw_output,
                    "latency_ms": int(r.latency_seconds * 1000),
                    "resized_to": list(r.image_size_after_resize),
                    "phase": 2,
                    "model": parser_module.MODEL_ID,
                    "error": r.error,
                }
                if r.parsed is None:
                    fail_count += 1
                else:
                    ok_count += 1
            except Exception as e:
                rec = {
                    "src_path": str(img.relative_to(REPO_ROOT)),
                    "ocr_result": None,
                    "raw_output": "",
                    "latency_ms": 0,
                    "resized_to": None,
                    "phase": 2,
                    "model": parser_module.MODEL_ID,
                    "error": f"exception: {type(e).__name__}: {e}",
                }
                fail_count += 1
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            if i % 5 == 0 or i == len(todo):
                elapsed = time.perf_counter() - t_start
                avg = elapsed / i
                eta = (len(todo) - i) * avg
                print(
                    f"[phase2] {i}/{len(todo)} | ok={ok_count} fail={fail_count} | "
                    f"avg={avg:.1f}s | RAM={rss_gb():.2f}GB | "
                    f"elapsed={elapsed / 60:.1f}min | eta={eta / 60:.1f}min",
                    flush=True,
                )

    total_t = time.perf_counter() - t_start
    print(f"\n[phase2] ============= SUMMARY =============")
    print(f"[phase2] processed: {len(todo)}")
    print(f"[phase2] ok       : {ok_count}")
    print(f"[phase2] fail     : {fail_count} ({100 * fail_count / max(1, len(todo)):.1f}%)")
    print(f"[phase2] total    : {total_t / 60:.1f} min")
    print(f"[phase2] avg      : {total_t / len(todo):.1f} s/image")
    print(f"[phase2] output   : {output_file}")
    print("[phase2] DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
