"""Single-image smoke test for the Ollama Vision provider.

Run once `ollama pull qwen2.5vl:7b` completes to verify:
  1. The model loads
  2. Our OllamaVisionProvider calls it correctly
  3. The response parser extracts callouts as expected
  4. Latency per image is measured (so we can estimate full-run duration)

Use the first non-tiny embedded image from the source xlsx.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dagster_project.ai.ollama_vision import OllamaVisionProvider  # noqa: E402
from dagster_project.ai.provider import EnrichmentRequest  # noqa: E402
from parser.image_extractor import extract_unique_images  # noqa: E402

log = logging.getLogger(__name__)


async def main_async() -> int:
    log.info("Extracting images...")
    images = extract_unique_images("../shared/sample-data/example.xlsx")
    # Skip very small images (likely logos) — find first schematic-sized one
    schematics = [img for img in images if img.size_bytes >= 50_000]
    if not schematics:
        log.error("No schematic-sized images found")
        return 1
    img = schematics[0]
    log.info(
        "Test image: sha256=%s ext=%s size=%d bytes from %d sheets",
        img.sha256[:16],
        img.extension,
        img.size_bytes,
        len(img.source_sheets),
    )

    provider = OllamaVisionProvider()
    log.info("Calling Ollama Vision (this is the first inference — model loads into RAM)...")
    t0 = time.perf_counter()
    await provider.enrich(
        EnrichmentRequest(
            id=f"smoke:{img.sha256}",
            field="extract_callouts",
            inputs={
                "image_b64": base64.b64encode(img.raw_bytes).decode("ascii"),
                "image_sha256": img.sha256,
            },
        )
    )
    cold_ms = (time.perf_counter() - t0) * 1000

    # Second call to measure warm latency (model already in RAM).
    t0 = time.perf_counter()
    response2 = await provider.enrich(
        EnrichmentRequest(
            id=f"smoke2:{img.sha256}",
            field="extract_callouts",
            inputs={
                "image_b64": base64.b64encode(img.raw_bytes).decode("ascii"),
                "image_sha256": img.sha256,
            },
        )
    )
    warm_ms = (time.perf_counter() - t0) * 1000

    print("")  # noqa: T201
    print("=" * 60)  # noqa: T201
    print(f"Cold latency  : {cold_ms:>8.0f} ms  (first call, model load + inference)")  # noqa: T201
    print(f"Warm latency  : {warm_ms:>8.0f} ms  (second call, inference only)")  # noqa: T201
    print(f"Callouts found: {response2.result}")  # noqa: T201
    print(f"Confidence    : {response2.confidence}")  # noqa: T201
    print("=" * 60)  # noqa: T201

    # Projection for full run
    total = 1586
    concurrency = 4
    eta_min = total * (warm_ms / 1000) / concurrency / 60
    print(  # noqa: T201
        f"\nProjected full run ({total} images × concurrency={concurrency}): "
        f"~{eta_min:.0f} minutes"
    )

    return 0 if response2.result else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(asyncio.run(main_async()))
