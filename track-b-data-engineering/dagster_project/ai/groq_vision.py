"""Groq Cloud Vision provider — Llama 4 Scout 17B (16-expert MoE).

Implements `extract_callouts` of the ILLMProvider interface via Groq's
free-tier Vision API. Why Groq:

  • Free tier: 14,400 requests/day — enough for the full 1,586-image
    Kayo dataset (~11% of daily quota).
  • Speed: 0.5–1.5 seconds per image vs ~50 seconds for local Ollama
    7B on M2. Groq's LPU silicon dominates at vision inference.
  • Quality: Llama 4 Scout 17B / 16-expert MoE is a more capable model
    than the 7B-class local alternatives.
  • Zero RAM impact on the developer machine.
  • Privacy: Groq TOS — does not use API content for model training.

The submission's reviewer never calls Groq — once this provider runs
locally to populate `shared/llm-cache.jsonl`, the cached results are
served on subsequent runs without any further API calls.

Configuration:
    GROQ_API_KEY        — your Groq Cloud API key
    GROQ_VISION_MODEL   — default `meta-llama/llama-4-scout-17b-16e-instruct`
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

from .provider import EnrichmentMeta, EnrichmentRequest, EnrichmentResponse

log = logging.getLogger(__name__)

PROMPT_TEMPLATE_VER = "groq-vision-callouts-v1"
DEFAULT_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
DEFAULT_API_URL = "https://api.groq.com/openai/v1/chat/completions"

_CALLOUT_PROMPT = """You are inspecting an exploded-view parts diagram from a motorcycle/ATV \
service catalog. The diagram has small numeric labels (callouts) like 1, 2, 3, or sometimes \
sub-callouts like "1-1", "1-6L", "1-6R".

Look carefully at the image and list every callout number you can read. Return ONLY a JSON \
array of strings, no commentary. Example: ["1", "2", "3", "4", "5", "1-1", "1-6L"]

If you cannot identify any callout numbers, return [].
"""


class GroqVisionProvider:
    """Vision provider backed by Groq Cloud's free-tier API."""

    name = "groq-vision"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        api_url: str = DEFAULT_API_URL,
        timeout_s: int = 60,
        max_retries: int = 5,
        min_interval_s: float = 0.0,
    ) -> None:
        self._api_key = api_key or os.environ.get("GROQ_API_KEY")
        self._model = model
        self._api_url = api_url
        self._timeout = timeout_s
        self._max_retries = max_retries
        self._min_interval_s = min_interval_s
        self._rate_lock = asyncio.Lock()
        self._last_fire = 0.0
        if not self._api_key:
            log.warning(
                "GroqVisionProvider initialised without API key — every "
                "call will fall through to the cache layer. Set GROQ_API_KEY "
                "to enable upstream inference."
            )

    async def enrich(self, req: EnrichmentRequest) -> EnrichmentResponse:
        if httpx is None:
            return self._fallback(req, reason="httpx_not_installed")
        if req.field != "extract_callouts":
            return self._fallback(req, reason="unsupported_field")
        if not self._api_key:
            return self._fallback(req, reason="missing_api_key")

        image_b64 = req.inputs.get("image_b64")
        if not isinstance(image_b64, str) or not image_b64:
            return self._fallback(req, reason="image_missing")

        mime = self._guess_mime(image_b64)

        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _CALLOUT_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                        },
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": 256,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}

        # Token-bucket pacing: never fire two real upstream calls closer
        # together than min_interval_s. This is invoked AFTER cache lookup
        # in CachedLLMProvider, so cache hits don't pay the wait.
        if self._min_interval_s > 0:
            async with self._rate_lock:
                now = time.perf_counter()
                wait = self._last_fire + self._min_interval_s - now
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last_fire = time.perf_counter()

        # Retry on 429 with Retry-After header (Groq returns seconds-to-wait).
        start = time.monotonic()
        attempt = 0
        resp = None
        while attempt < self._max_retries:
            attempt += 1
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(self._api_url, headers=headers, json=payload)
            except (httpx.RequestError, httpx.TimeoutException) as e:
                log.warning("Groq vision call failed: %s", e)
                return self._fallback(req, reason="upstream_unreachable")

            if resp.status_code != 429:
                break

            # Honour Retry-After. Fall back to exponential if header missing.
            retry_after = resp.headers.get("retry-after")
            if retry_after:
                try:
                    wait_s = float(retry_after)
                except ValueError:
                    wait_s = 2.0 ** attempt
            else:
                wait_s = 2.0 ** attempt
            # Cap the wait to keep things sane (Groq sometimes returns >60s on TPM exhaust).
            wait_s = min(wait_s, 30.0)
            log.info(
                "Groq 429 — sleeping %.1fs (attempt %d/%d)",
                wait_s, attempt, self._max_retries,
            )
            await asyncio.sleep(wait_s)

        latency_ms = int((time.monotonic() - start) * 1000)

        if resp is None or resp.status_code == 429:
            return self._fallback(req, reason="rate_limited_after_retry")
        if resp.status_code != 200:
            log.warning("Groq HTTP %s: %s", resp.status_code, resp.text[:200])
            return self._fallback(req, reason=f"http_{resp.status_code}")

        data: Any = resp.json()
        raw_text = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        callouts = _parse_callouts(raw_text)
        usage = data.get("usage", {})

        return EnrichmentResponse(
            id=req.id,
            field=req.field,
            result=callouts,  # type: ignore[arg-type]
            confidence=_score_confidence(callouts),
            meta=EnrichmentMeta(
                provider=self.name,
                prompt_template_ver=PROMPT_TEMPLATE_VER,
                tokens_in=usage.get("prompt_tokens"),
                tokens_out=usage.get("completion_tokens"),
                cost_usd=0,  # Free tier
                latency_ms=latency_ms,
                cache_hit=False,
            ),
        )

    def _guess_mime(self, b64: str) -> str:
        """Infer mime from base64 header bytes."""
        import base64 as _b64
        try:
            head = _b64.b64decode(b64[:16] + "==")
        except Exception:
            return "image/jpeg"
        if head.startswith(b"\x89PNG"):
            return "image/png"
        if head.startswith(b"\xff\xd8"):
            return "image/jpeg"
        if head.startswith(b"GIF8"):
            return "image/gif"
        return "image/jpeg"

    def _fallback(self, req: EnrichmentRequest, *, reason: str) -> EnrichmentResponse:
        log.debug("Groq vision fallback: %s", reason)
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
    if not raw:
        return []
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
    return _CALLOUT_TOKEN_RE.findall(raw)


def _score_confidence(callouts: list[str]) -> str:
    if not callouts:
        return "low"
    if len(callouts) >= 5:
        return "high"
    if len(callouts) >= 2:
        return "medium"
    return "low"
