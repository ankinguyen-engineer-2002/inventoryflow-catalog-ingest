"""Batch vision OCR — run Qwen2.5-VL on every extracted schematic image.

Supports slicing for parallel execution:
    python batch_vision_ocr.py                   # all images, single output
    python batch_vision_ocr.py --slice 0/3       # worker 0 of 3
    python batch_vision_ocr.py --slice 1/3       # worker 1 of 3
    python batch_vision_ocr.py --slice 2/3       # worker 2 of 3

Each slice writes to `shared/mlx-vision-output.<slice_index>.jsonl`.
Merge with: `cat shared/mlx-vision-output.*.jsonl > shared/mlx-vision-output.jsonl`

Outputs JSONL with one record per image:
- sha256: image content hash (from filename)
- src_path: relative path to image
- ocr_result: parsed JSON from the vision LLM (list of callout objects)
- raw_output: raw LLM string output
- latency_ms: per-image inference latency
- resized_to: (width, height) after preprocessing
- error: optional error string

Progress: prints every 5 images with running totals + RAM peak.
"""
from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from pathlib import Path

from parser import VisionParser, ParserConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
IMAGE_DIR = Path(__file__).resolve().parent / "extracted_images"


def rss_gb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if rss > 1024 * 1024 * 1024 * 1024:  # macOS reports bytes
        rss = rss // 1024
    return rss / 1024 / 1024 / 1024


def collect_images() -> list[Path]:
    images: list[Path] = []
    for ext in ("*.png", "*.jpg", "*.jpeg"):
        images.extend(IMAGE_DIR.rglob(ext))
    # Skip ".resized" intermediates if any exist from prior runs
    images = [p for p in images if ".resized" not in p.name]
    return sorted(set(images))


def parse_slice(arg: str | None) -> tuple[int, int]:
    if arg is None:
        return 0, 1
    idx, total = arg.split("/")
    return int(idx), int(total)


def slice_images(images: list[Path], idx: int, total: int) -> list[Path]:
    if total == 1:
        return images
    return [img for i, img in enumerate(images) if i % total == idx]


def load_done_sha256s(output_file: Path) -> set[str]:
    """Read existing output file to identify already-processed images by sha256.
    Returns empty set if file doesn't exist (fresh run)."""
    if not output_file.exists():
        return set()
    done: set[str] = set()
    with output_file.open() as f:
        for line in f:
            try:
                d = json.loads(line)
                done.add(d["sha256"])
            except Exception:
                continue
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slice", type=str, default=None, help="N/M for parallel workers")
    ap.add_argument("--resume", action="store_true",
                    help="Skip images whose sha256 already in output file (append mode)")
    args = ap.parse_args()

    idx, total = parse_slice(args.slice)
    all_images = collect_images()
    images = slice_images(all_images, idx, total)
    output_file = REPO_ROOT / "shared" / (
        f"mlx-vision-output.{idx}.jsonl" if total > 1 else "mlx-vision-output.jsonl"
    )

    # Resume support: if --resume, filter out images already done
    if args.resume:
        done_shas = load_done_sha256s(output_file)
        before = len(images)
        # The filename embeds first 12 chars of sha256; match against full sha
        def short_sha_of(p: Path) -> str:
            stem = p.stem.split("_", 1)[-1] if "_" in p.stem else p.stem
            return stem
        # Build set of short shas already done
        short_done = {s[:12] for s in done_shas}
        images = [p for p in images if short_sha_of(p) not in short_done]
        print(f"[batch] --resume: {before - len(images)} already done, "
              f"{len(images)} remaining in this slice")

    print(f"[batch] worker {idx}/{total} | "
          f"slice {len(images)}/{len(all_images)} images")
    print(f"[batch] writing output to {output_file} (mode={'append' if args.resume else 'write'})")
    print(f"[batch] baseline RAM: {rss_gb():.2f} GB")
    OUTPUT_FILE = output_file  # noqa: N806 — preserve downstream reference
    OPEN_MODE = "a" if args.resume else "w"  # noqa: N806

    cfg = ParserConfig()
    print(f"[batch] config: max_pixels={cfg.max_pixels}, kv_bits={cfg.kv_bits}, "
          f"max_tokens={cfg.max_tokens}, resize_edge={cfg.resize_longest_edge}")

    parser = VisionParser(cfg)
    t_load_start = time.perf_counter()
    parser.load()
    t_load = time.perf_counter() - t_load_start
    print(f"[batch] model loaded in {t_load:.1f}s, RAM={rss_gb():.2f} GB")

    succeeded = 0
    parse_failures = 0
    total_latency = 0.0
    t_run_start = time.perf_counter()

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open(OPEN_MODE) as out:
        for i, img in enumerate(images, 1):
            sha256 = img.stem.split("_", 1)[-1] if "_" in img.stem else img.stem
            try:
                result = parser.parse(img)
                record = {
                    "sha256": sha256,
                    "src_path": str(img.relative_to(REPO_ROOT)),
                    "ocr_result": result.parsed,
                    "raw_output": result.raw_output,
                    "latency_ms": int(result.latency_seconds * 1000),
                    "resized_to": list(result.image_size_after_resize),
                    "error": result.error,
                }
                if result.parsed is None:
                    parse_failures += 1
                else:
                    succeeded += 1
                total_latency += result.latency_seconds
            except Exception as e:
                record = {
                    "sha256": sha256,
                    "src_path": str(img.relative_to(REPO_ROOT)),
                    "ocr_result": None,
                    "raw_output": "",
                    "latency_ms": 0,
                    "resized_to": None,
                    "error": f"exception: {type(e).__name__}: {e}",
                }
                parse_failures += 1
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            if i % 10 == 0 or i == len(images):
                elapsed = time.perf_counter() - t_run_start
                avg = total_latency / max(i, 1)
                eta_s = (len(images) - i) * avg
                pct = i * 100 // len(images)
                print(
                    f"[batch] {i}/{len(images)} ({pct}%) | "
                    f"ok={succeeded} fail={parse_failures} | "
                    f"avg={avg:.2f}s | RAM={rss_gb():.2f}GB | "
                    f"elapsed={elapsed/60:.1f}min | eta={eta_s/60:.1f}min",
                    flush=True,
                )

    total = time.perf_counter() - t_run_start
    print("\n[batch] ============= SUMMARY =============")
    print(f"[batch] images:        {len(images)}")
    print(f"[batch] succeeded:     {succeeded}")
    print(f"[batch] json failures: {parse_failures}")
    print(f"[batch] total wall:    {total/60:.1f} min")
    print(f"[batch] avg latency:   {total_latency/len(images):.2f}s")
    print(f"[batch] peak RAM:      {rss_gb():.2f} GB")
    print(f"[batch] output:        {OUTPUT_FILE}")
    print(f"[batch] DONE")
    return 0 if parse_failures < len(images) // 4 else 1


if __name__ == "__main__":
    raise SystemExit(main())
