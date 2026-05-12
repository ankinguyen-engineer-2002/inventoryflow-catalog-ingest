"""OpenRouter Vision provider — multi-upstream proxy for vision models.

Implements `extract_callouts` of the ILLMProvider interface via the
OpenRouter Cloud API, an aggregator that routes to 200+ models from
many providers (OpenAI, Anthropic, Google, Meta, Qwen, Mistral, NVIDIA,
Baidu) behind a single API key + OpenAI-compatible payload format.

Why OpenRouter for this submission:

  • Default model `baidu/qianfan-ocr-fast:free` is an OCR-specialised
    vision model — exactly the right tool for reading callout numbers
    off exploded-view parts diagrams. Free tier sponsored by Baidu.
  • Switch upstream with one env var (`OPENROUTER_VISION_MODEL`) to
    any of: anthropic/claude-3.5-sonnet, openai/gpt-4o,
    google/gemini-pro-vision, etc. without touching code.
  • Production-grade fall-back path when in-house Ollama / Groq
    free-tier quota is exhausted: bump to paid tier on the same
    provider, change one env var.
"""

from __future__ import annotations

import asyncio
import base64 as _b64
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

PROMPT_TEMPLATE_VER = "openrouter-vision-callouts-v1"
DEFAULT_MODEL = "baidu/qianfan-ocr-fast:free"
DEFAULT_API_URL = "https://openrouter.ai/api/v1/chat/completions"

_CALLOUT_PROMPT = """Look at this exploded-view parts diagram from a motorcycle/ATV service \
catalog. List every callout number you can read in the image (numbers like 1, 2, 3, or sub-callouts \
like 1-1, 1-6L, 1-6R).

Return ONLY a JSON array of strings, no commentary. Example: ["1", "2", "3", "1-1", "1-6L"]

If you cannot identify any callout numbers, return [].
"""


class OpenRouterVisionProvider:
    """Vision provider backed by OpenRouter's free-tier vision routes."""

    name = "openrouter-vision"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        api_url: str = DEFAULT_API_URL,
        timeout_s: int = 120,
        max_retries: int = 5,
        min_interval_s: float = 0.0,
        referer: str = "https://github.com/ankinguyen-engineer-2002/inventoryflow-catalog-ingest",
        app_title: str = "InventoryFlow Catalog Ingest",
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self._model = model
        self._api_url = api_url
        self._timeout = timeout_s
        self._max_retries = max_retries
        self._min_interval_s = min_interval_s
        self._referer = referer
        self._app_title = app_title
        self._rate_lock = asyncio.Lock()
        self._last_fire = 0.0

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
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "HTTP-Referer": self._referer,
            "X-Title": self._app_title,
        }

        # Token-bucket pacing — only invoked after CachedLLMProvider miss.
        if self._min_interval_s > 0:
            async with self._rate_lock:
                now = time.perf_counter()
                wait = self._last_fire + self._min_interval_s - now
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last_fire = time.perf_counter()

        start = time.monotonic()
        attempt = 0
        resp = None
        while attempt < self._max_retries:
            attempt += 1
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(self._api_url, headers=headers, json=payload)
            except (httpx.RequestError, httpx.TimeoutException) as e:
                log.warning("OpenRouter vision call failed: %s", e)
                return self._fallback(req, reason="upstream_unreachable")

            if resp.status_code != 429:
                break

            retry_after = resp.headers.get("retry-after")
            if retry_after:
                try:
                    wait_s = float(retry_after)
                except ValueError:
                    wait_s = 2.0 ** attempt
            else:
                wait_s = 2.0 ** attempt
            wait_s = min(wait_s, 30.0)
            log.info(
                "OpenRouter 429 — sleeping %.1fs (attempt %d/%d)",
                wait_s, attempt, self._max_retries,
            )
            await asyncio.sleep(wait_s)

        latency_ms = int((time.monotonic() - start) * 1000)

        if resp is None or resp.status_code == 429:
            return self._fallback(req, reason="rate_limited_after_retry")
        if resp.status_code != 200:
            log.warning("OpenRouter HTTP %s: %s", resp.status_code, resp.text[:200])
            return self._fallback(req, reason=f"http_{resp.status_code}")

        data: Any = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return self._fallback(req, reason="empty_choices")
        raw_text = choices[0].get("message", {}).get("content", "") or ""
        callouts = _parse_callouts(raw_text)
        usage = data.get("usage", {}) or {}

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
                cost_usd=0,
                latency_ms=latency_ms,
                cache_hit=False,
            ),
        )

    def _guess_mime(self, b64: str) -> str:
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
        log.debug("OpenRouter vision fallback: %s", reason)
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
