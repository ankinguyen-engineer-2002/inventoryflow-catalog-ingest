"""Run Ollama Vision on every embedded image in the source xlsx.

Extracts all images from xl/media/, dedupes (already byte-unique in this
dataset — 1,586 images), encodes each to base64, sends to qwen2.5vl:7b
via the local Ollama daemon, parses callout numbers from the response,
and writes results to the shared LLM cache file.

The shared cache is the same JSONL Track A reads + Track B reads. Once
this script populates it, both tracks see vision-extracted callouts
without making any further LLM calls.

Estimated runtime: ~2-3 hours sequential, ~30-60 minutes with
concurrency=4 on Apple M2 (8 GB+ RAM, 16 GB recommended).

Run with Ollama daemon serving:
    ollama serve   # if app isn't running
    python3 scripts/vision_extract_all.py
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dagster_project.ai.cached import CachedLLMProvider  # noqa: E402
from dagster_project.ai.gemini_vision import GeminiVisionProvider  # noqa: E402
from dagster_project.ai.groq_vision import GroqVisionProvider  # noqa: E402
from dagster_project.ai.ollama_vision import OllamaVisionProvider  # noqa: E402
from dagster_project.ai.openrouter_vision import OpenRouterVisionProvider  # noqa: E402
from dagster_project.ai.provider import EnrichmentRequest  # noqa: E402
from parser.image_extractor import extract_unique_images  # noqa: E402

log = logging.getLogger(__name__)


async def process_image(
    provider, image, semaphore: asyncio.Semaphore, idx: int, total: int
) -> dict:  # type: ignore[no-untyped-def]
    async with semaphore:
        b64 = base64.b64encode(image.raw_bytes).decode("ascii")
        request = EnrichmentRequest(
            id=f"vision:{image.sha256}",
            field="extract_callouts",
            inputs={"image_b64": b64, "image_sha256": image.sha256},
        )
        t0 = time.perf_counter()
        response = await provider.enrich(request)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        callouts = response.result if isinstance(response.result, list) else []
        log.info(
            "[%4d/%4d] %s → %d callouts (%.0f ms) cache_hit=%s",
            idx + 1, total, image.sha256[:12], len(callouts), elapsed_ms,
            response.meta.cache_hit,
        )
        return {
            "sha256": image.sha256,
            "extension": image.extension,
            "size_bytes": image.size_bytes,
            "source_sheets": list(image.source_sheets),
            "callouts": callouts,
            "confidence": response.confidence,
            "latency_ms": int(elapsed_ms),
            "cache_hit": response.meta.cache_hit,
        }


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vision_extract_all")
    parser.add_argument("--xlsx", default="../shared/sample-data/example.xlsx")
    parser.add_argument(
        "--cache",
        default="../shared/llm-cache.jsonl",
        help="Shared cache file path (read by both tracks)",
    )
    parser.add_argument(
        "--out",
        default="../sample-output/vision-extracted-callouts.json",
        help="Per-image results dump for diffing / sample-output",
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0,
                        help="Cap to N images (0 = all)")
    parser.add_argument("--min-size-bytes", type=int, default=0,
                        help="Skip images smaller than this (likely icons)")
    parser.add_argument(
        "--provider",
        choices=["ollama", "groq", "openrouter", "gemini"],
        default="ollama",
        help="Which vision upstream to use",
    )
    parser.add_argument(
        "--min-interval-s",
        type=float,
        default=0.0,
        help="Minimum seconds between requests (token-bucket pacing). "
             "For Groq free tier set ~3.5 to stay under 18 req/min on Llama-4 Scout.",
    )
    args = parser.parse_args(argv)

    log.info("Extracting images from %s", args.xlsx)
    images = extract_unique_images(args.xlsx)
    log.info("Found %d unique images", len(images))

    if args.min_size_bytes > 0:
        images = [i for i in images if i.size_bytes >= args.min_size_bytes]
        log.info("After size filter (>= %d bytes): %d images", args.min_size_bytes, len(images))

    if args.limit > 0:
        images = images[: args.limit]
        log.info("Limited to first %d images", len(images))

    if args.provider == "groq":
        upstream = GroqVisionProvider(min_interval_s=args.min_interval_s)
    elif args.provider == "openrouter":
        upstream = OpenRouterVisionProvider(min_interval_s=args.min_interval_s)
    elif args.provider == "gemini":
        upstream = GeminiVisionProvider(min_interval_s=args.min_interval_s)
    else:
        upstream = OllamaVisionProvider()
    log.info("Using upstream: %s", upstream.name)
    if args.min_interval_s > 0:
        log.info("Token-bucket pacing: min %.2fs between upstream calls", args.min_interval_s)
    provider = CachedLLMProvider(upstream, args.cache)

    semaphore = asyncio.Semaphore(args.concurrency)
    t_start = time.perf_counter()
    tasks = [
        process_image(provider, img, semaphore, i, len(images))
        for i, img in enumerate(images)
    ]
    results = await asyncio.gather(*tasks)
    total_elapsed = time.perf_counter() - t_start

    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2) + "\n")

    cache_hits = sum(1 for r in results if r["cache_hit"])
    with_callouts = sum(1 for r in results if r["callouts"])
    avg_latency = sum(r["latency_ms"] for r in results) / max(len(results), 1)

    print("")  # noqa: T201
    print("=== Vision extraction complete ===")  # noqa: T201
    print(f"  Images processed     : {len(results)}")  # noqa: T201
    print(f"  Cache hits           : {cache_hits}")  # noqa: T201
    print(f"  Images with callouts : {with_callouts}")  # noqa: T201
    print(f"  Total wall time      : {total_elapsed:.1f}s "  # noqa: T201
          f"({total_elapsed / 60:.1f} min)")
    print(f"  Avg latency / image  : {avg_latency:.0f} ms")  # noqa: T201
    print(f"  Results JSON         : {out_path}")  # noqa: T201
    print(f"  Cache file           : {Path(args.cache).resolve()}")  # noqa: T201
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    sys.exit(main())
