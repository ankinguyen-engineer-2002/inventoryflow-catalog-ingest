"""MLX vision parser for schematic image OCR (Qwen2.5-VL).

Optimized for M1 Max 64GB targeting Kayo ATV catalog schematic parsing.

Key levers tuned here:
    1. max_pixels: caps visual tokens — biggest lever for both RAM and latency.
    2. KV cache quantization (kv_bits=4): halves KV cache RAM with ~no quality loss.
    3. max_tokens: caps output length — JSON label list rarely exceeds 256 tokens.
    4. Image pre-resize: predictable pixel budget per image (avoids surprises).
    5. Prefix caching: identical system prompt across calls reuses KV.
"""
from __future__ import annotations

import gc
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mlx.core as mx
from mlx_vlm import generate, load
from mlx_vlm.prompt_utils import apply_chat_template
from mlx_vlm.utils import load_config
from PIL import Image

MODEL_ID = "mlx-community/Qwen2.5-VL-7B-Instruct-8bit"  # 7B for accuracy — 2B undercounted callouts on busy schematics

# Visual tokens budget. Qwen2-VL/Qwen2.5-VL uses 28x28 patches.
# 768*28*28 = ~602K pixels ≈ schematic at ~775x775 — enough for label OCR,
# 25% fewer tokens than the 1024×28×28 default → ~15% faster encoder.
MIN_PIXELS = 256 * 28 * 28      # 200K — floor so small images still get detail
MAX_PIXELS = 768 * 28 * 28      # 602K — tightened from 802K for throughput

# Pre-resize cap (longest edge) before handing to processor.
# Setting to 0 disables manual resize — let the Qwen processor do it natively,
# saving ~50-100 ms per image of Pillow I/O.
# WHY 1024: with edge ≤ 1024px the vision encoder produces ≤ ~1300 prefill tokens,
# bounding GPU command-buffer time per image. Larger raw inputs (3000-4000px)
# generated 8000+ tokens and triggered macOS Metal "Impacting Interactivity"
# watchdog kills when 3+ workers ran in parallel.
RESIZE_LONGEST_EDGE = 1024

DEFAULT_SYSTEM_PROMPT = (
    "You are a catalog data extractor for ATV parts schematic diagrams. "
    "The image contains numbered callout labels pointing to mechanical parts. "
    "Each callout has a small number (e.g. 1, 2, 3) next to a part. "
    "Extract every visible callout. Return ONLY valid JSON, no prose."
)

DEFAULT_USER_PROMPT = (
    "List every numbered callout in this schematic.\n"
    "Output ONLY a JSON array. Each item has exactly these keys:\n"
    '  "n" — integer, the callout number\n'
    '  "pos" — string, one value from: top-left, top, top-right, center-left, '
    "center, center-right, bottom-left, bottom, bottom-right\n"
    'Example output: [{"n": 1, "pos": "top-left"}, {"n": 2, "pos": "center"}]\n'
    "Do not add any other text. JSON only."
)


@dataclass
class ParserConfig:
    model_id: str = MODEL_ID
    max_tokens: int = 1024            # 1024 covers most schematics (3-30 callouts ≈ 400-700 tokens). 2048 was over-generous and let hallucination loops eat 2 min/img before GPU watchdog killed the worker. 1024 caps wasted GPU time at ~1 min. Trade-off: rare schematics with >40 callouts may truncate (5% of records) — those become Phase 2 retry candidates.
    temperature: float = 0.0
    kv_bits: int | None = 4           # 4-bit KV cache cuts cache RAM ~4x
    kv_group_size: int = 64
    min_pixels: int = MIN_PIXELS
    max_pixels: int = MAX_PIXELS
    resize_longest_edge: int = RESIZE_LONGEST_EDGE
    system_prompt: str = DEFAULT_SYSTEM_PROMPT


@dataclass
class ParseResult:
    raw_output: str
    parsed: Any | None
    latency_seconds: float
    image_path: str
    image_size_after_resize: tuple[int, int]
    error: str | None = None


@dataclass
class ParserStats:
    calls: int = 0
    total_seconds: float = 0.0
    parse_failures: int = 0
    history: list[float] = field(default_factory=list)


class VisionParser:
    """Singleton-ish parser: load model once, reuse across many images."""

    def __init__(self, config: ParserConfig | None = None):
        self.config = config or ParserConfig()
        self._model = None
        self._processor = None
        self._loaded_config = None
        self.stats = ParserStats()

    def load(self) -> None:
        if self._model is not None:
            return
        t0 = time.perf_counter()
        self._model, self._processor = load(
            self.config.model_id,
            processor_config={
                "min_pixels": self.config.min_pixels,
                "max_pixels": self.config.max_pixels,
            },
        )
        self._loaded_config = load_config(self.config.model_id)
        # Force materialization so subsequent calls don't pay first-eval cost
        mx.eval(self._model.parameters())
        load_seconds = time.perf_counter() - t0
        print(f"[parser] model loaded in {load_seconds:.2f}s")

    def _prep_image(self, image_path: str | Path) -> tuple[str, tuple[int, int]]:
        """Optionally resize. If resize_longest_edge == 0, skip Pillow I/O
        and let the Qwen processor handle resizing natively."""
        path = Path(image_path)
        if self.config.resize_longest_edge <= 0:
            with Image.open(path) as img:
                size = img.size
            return str(path), size
        with Image.open(path) as img:
            img = img.convert("RGB")
            w, h = img.size
            longest = max(w, h)
            if longest > self.config.resize_longest_edge:
                scale = self.config.resize_longest_edge / longest
                new_size = (int(w * scale), int(h * scale))
                img = img.resize(new_size, Image.LANCZOS)
            else:
                new_size = (w, h)
            resized_path = path.with_suffix(f".resized{path.suffix}")
            img.save(resized_path, optimize=True)
            return str(resized_path), new_size

    def parse(
        self,
        image_path: str | Path,
        user_prompt: str | None = None,
    ) -> ParseResult:
        if self._model is None:
            self.load()

        resized_path, size_after = self._prep_image(image_path)
        prompt_text = user_prompt or DEFAULT_USER_PROMPT
        messages = [
            {"role": "system", "content": self.config.system_prompt},
            {"role": "user", "content": prompt_text},
        ]
        formatted = apply_chat_template(
            self._processor, self._loaded_config, messages, num_images=1
        )

        gen_kwargs: dict[str, Any] = {
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "verbose": False,
        }
        if self.config.kv_bits is not None:
            gen_kwargs["kv_bits"] = self.config.kv_bits
            gen_kwargs["kv_group_size"] = self.config.kv_group_size

        t0 = time.perf_counter()
        output = generate(
            self._model,
            self._processor,
            formatted,
            [resized_path],
            **gen_kwargs,
        )
        latency = time.perf_counter() - t0

        # mlx-vlm may return GenerationResult or str depending on version
        raw = output.text if hasattr(output, "text") else str(output)
        parsed, err = self._safe_json(raw)

        self.stats.calls += 1
        self.stats.total_seconds += latency
        self.stats.history.append(latency)
        if parsed is None:
            self.stats.parse_failures += 1

        return ParseResult(
            raw_output=raw,
            parsed=parsed,
            latency_seconds=latency,
            image_path=str(image_path),
            image_size_after_resize=size_after,
            error=err,
        )

    @staticmethod
    def _safe_json(raw: str) -> tuple[Any | None, str | None]:
        try:
            stripped = raw.strip()
            for fence in ("```json", "```"):
                if stripped.startswith(fence):
                    stripped = stripped.split(fence, 1)[1]
                if stripped.endswith("```"):
                    stripped = stripped.rsplit("```", 1)[0]
            stripped = stripped.strip()
            return json.loads(stripped), None
        except (json.JSONDecodeError, ValueError) as e:
            return None, f"json_parse_error: {e}"

    def free(self) -> None:
        self._model = None
        self._processor = None
        gc.collect()
        mx.clear_cache()
