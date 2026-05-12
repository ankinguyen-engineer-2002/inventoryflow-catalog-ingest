"""Ollama Vision provider — qwen2.5vl:7b local inference.

Implements the `extract_callouts` field of the ILLMProvider interface.
Sends a base64-encoded schematic image to the local Ollama daemon and
parses the response for callout numbers (1, 2, 3, 1-1, 1-6L, etc.).

Default model: `qwen2.5vl:7b` — Qwen2.5-VL 7B, the strongest open-source
multilingual VLM in the 7B class (Bai et al., 2024 + 2025 update).
Self-hosted, 4.7 GB on disk; runs anywhere with 8 GB RAM.

Why local Ollama instead of cloud (Claude/GPT Vision):
  • Dealer schematics are proprietary OEM artefacts. Production deploy
    can't ship them to external cloud APIs without dealer consent.
  • Free, deterministic latency, no rate-limit dance.
  • One env-var switch (LLM_PROVIDER=anthropic) graduates to cloud later.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

from .provider import EnrichmentMeta, EnrichmentRequest, EnrichmentResponse

log = logging.getLogger(__name__)

PROMPT_TEMPLATE_VER = "ollama-vision-callouts-v1"

_CALLOUT_PROMPT = """You are inspecting an exploded-view parts diagram from a motorcycle/ATV \
service catalog. The diagram has small numeric labels (callouts) like 1, 2, 3, or sometimes \
sub-callouts like "1-1", "1-6L", "1-6R".

Look carefully at the image and list every callout number you can read. Return ONLY a JSON \
array of strings, no commentary. Example: ["1", "2", "3", "4", "5", "1-1", "1-6L"]

If you cannot identify any callout numbers, return [].
"""


class OllamaVisionProvider:
    """Vision provider backed by a local Ollama daemon.

    Supports only the `extract_callouts` field. Translation requests
    fall back to a None result so the cached/handoff layers can pick
    them up.
    """

    name = "ollama-vision"

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5vl:7b",
        timeout_s: int = 120,
    ) -> None:
        self._base_url = base_url
        self._model = model
        self._timeout = timeout_s

    async def enrich(self, req: EnrichmentRequest) -> EnrichmentResponse:
        if httpx is None:
            return self._fallback(req, reason="httpx_not_installed")
        if req.field != "extract_callouts":
            return self._fallback(req, reason="unsupported_field")

        image_b64 = self._resolve_image(req)
        if image_b64 is None:
            return self._fallback(req, reason="image_missing_or_invalid")

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/api/generate",
                    json={
                        "model": self._model,
                        "prompt": _CALLOUT_PROMPT,
                        "images": [image_b64],
                        "stream": False,
                        "options": {
                            "temperature": 0,
                            "top_p": 1,
                            "num_predict": 256,
                        },
                    },
                )
        except (httpx.RequestError, httpx.TimeoutException) as e:
            log.warning("Ollama vision call failed: %s", e)
            return self._fallback(req, reason="upstream_unreachable")

        latency_ms = int((time.monotonic() - start) * 1000)

        if resp.status_code != 200:
            return self._fallback(req, reason=f"http_{resp.status_code}")

        data: Any = resp.json()
        callouts = _parse_callouts(data.get("response", ""))
        confidence = _score_confidence(callouts)

        return EnrichmentResponse(
            id=req.id,
            field=req.field,
            result=callouts,  # type: ignore[arg-type]
            confidence=confidence,
            meta=EnrichmentMeta(
                provider=self.name,
                prompt_template_ver=PROMPT_TEMPLATE_VER,
                tokens_in=data.get("prompt_eval_count"),
                tokens_out=data.get("eval_count"),
                cost_usd=0,
                latency_ms=latency_ms,
                cache_hit=False,
            ),
        )

    def _resolve_image(self, req: EnrichmentRequest) -> str | None:
        """Inputs may carry either an `image_b64` field already encoded,
        or an `image_path` pointing at the file on disk.
        """
        existing = req.inputs.get("image_b64")
        if existing:
            return existing if isinstance(existing, str) else None
        path = req.inputs.get("image_path")
        if path and isinstance(path, str):
            try:
                with open(path, "rb") as f:
                    return base64.b64encode(f.read()).decode("ascii")
            except OSError:
                return None
        return None

    def _fallback(self, req: EnrichmentRequest, *, reason: str) -> EnrichmentResponse:
        log.debug("Ollama vision fallback: %s", reason)
        return EnrichmentResponse(
            id=req.id,
            field=req.field,
            result=None,
            confidence="low",
            meta=EnrichmentMeta(
                provider=self.name,
                prompt_template_ver=PROMPT_TEMPLATE_VER,
                cache_hit=False,
                cost_usd=0,
            ),
        )


_CALLOUT_TOKEN_RE = re.compile(r'"([\d]+(?:-[\d]+[A-Z]?)?(?:\.\d+)?)"')


def _parse_callouts(raw: str) -> list[str]:
    """Extract callout strings from the model's JSON-ish response.

    qwen2.5vl usually returns clean JSON, but sometimes wraps it in
    markdown code fences or adds prose. Be lenient: pull every quoted
    callout-shaped token from the response.
    """
    if not raw:
        return []
    # Try strict JSON first.
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s)
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
    except json.JSONDecodeError:
        pass
    # Fallback: regex extract.
    return _CALLOUT_TOKEN_RE.findall(raw)


def _score_confidence(callouts: list[str]) -> str:
    if not callouts:
        return "low"
    if len(callouts) >= 5:
        return "high"
    if len(callouts) >= 2:
        return "medium"
    return "low"
