"""Smoke test: parse one schematic image, report RAM peak + latency.

Usage:
    python smoke_test.py <image_path> [<image_path> ...]

If no path provided, defaults to first PNG under sample-output/images/.
"""
from __future__ import annotations

import json
import os
import resource
import sys
import time
from pathlib import Path

from parser import VisionParser, ParserConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IMAGE_DIR = REPO_ROOT / "sample-output" / "images"


def rss_gb() -> float:
    """Current process resident set size in GB (macOS reports bytes)."""
    rss_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # On macOS ru_maxrss is bytes; on Linux it's KB. Detect via plausibility.
    if rss_bytes > 1024 * 1024 * 1024 * 1024:  # > 1TB → wrong units
        rss_bytes = rss_bytes // 1024
    return rss_bytes / 1024 / 1024 / 1024


def pick_default_images(n: int = 3) -> list[Path]:
    if not DEFAULT_IMAGE_DIR.exists():
        return []
    candidates = sorted(DEFAULT_IMAGE_DIR.glob("*.png")) + sorted(
        DEFAULT_IMAGE_DIR.glob("*.jpg")
    )
    return candidates[:n]


def main() -> int:
    args = sys.argv[1:]
    images = [Path(p) for p in args] if args else pick_default_images()
    if not images:
        print("ERROR: no images found and none supplied", file=sys.stderr)
        return 1

    print(f"[smoke] testing {len(images)} image(s)")
    print(f"[smoke] python pid={os.getpid()}")
    print(f"[smoke] baseline RAM: {rss_gb():.2f} GB")

    cfg = ParserConfig()
    print(f"[smoke] config: max_pixels={cfg.max_pixels}, kv_bits={cfg.kv_bits}, "
          f"max_tokens={cfg.max_tokens}, resize_edge={cfg.resize_longest_edge}")

    parser = VisionParser(cfg)
    t_load_start = time.perf_counter()
    parser.load()
    t_load = time.perf_counter() - t_load_start
    print(f"[smoke] after model load RAM: {rss_gb():.2f} GB (load {t_load:.1f}s)")

    results = []
    for i, img in enumerate(images, 1):
        if not img.exists():
            print(f"[smoke] skip missing: {img}")
            continue
        size_bytes = img.stat().st_size
        print(f"\n[smoke] #{i} {img.name} ({size_bytes/1024:.0f} KB)")
        result = parser.parse(img)
        print(f"[smoke]   latency: {result.latency_seconds:.2f}s")
        print(f"[smoke]   resized: {result.image_size_after_resize}")
        print(f"[smoke]   peak RAM: {rss_gb():.2f} GB")
        if result.parsed is not None:
            preview = json.dumps(result.parsed)[:240]
            print(f"[smoke]   parsed: {preview}")
        else:
            print(f"[smoke]   raw (no JSON): {result.raw_output[:240]!r}")
            print(f"[smoke]   err: {result.error}")
        results.append(result)

    if not results:
        print("[smoke] no successful results")
        return 2

    n = len(results)
    total = sum(r.latency_seconds for r in results)
    failures = sum(1 for r in results if r.parsed is None)
    print("\n[smoke] ============= SUMMARY =============")
    print(f"[smoke] images:        {n}")
    print(f"[smoke] avg latency:   {total/n:.2f}s")
    print(f"[smoke] total wall:    {total:.2f}s")
    print(f"[smoke] json failures: {failures}/{n}")
    print(f"[smoke] final RAM:     {rss_gb():.2f} GB")
    print(f"[smoke] est for 1586:  {total/n*1586/60:.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
