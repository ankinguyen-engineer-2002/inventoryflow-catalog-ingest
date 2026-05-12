"""Google Gemini Vision provider — gemini-2.5-flash free tier.

Implements `extract_callouts` of the ILLMProvider interface via Google's
Generative Language API. Distinct from the other providers in that
Gemini uses Google's own request schema (not OpenAI-compatible):

    POST /v1beta/models/<model>:generateContent?key=<API_KEY>
    { "contents": [{ "parts": [{ "text": ... }, { "inline_data": ... }] }] }

Free tier characteristics (2026-Q2):
  • 1,500 requests/day on gemini-2.5-flash
  • 10 RPM (requests per minute)
  • 1,000,000 TPM (tokens per minute) — effectively unconstrained
  • Vision-capable

Scope note: per ADR-007, Gemini's TOS allows Google to train models on
free-tier API content, which is **incompatible with proprietary dealer
data in production**. This provider is acceptable for the hiring-test
demo because the test xlsx (Kayo ATV) is a public sample distributed
with the test packet, not real dealer data. Production deployment
substitutes Anthropic Vision or self-hosted Ollama via env-var switch.
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

PROMPT_TEMPLATE_VER = "gemini-vision-callouts-v1"
DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

_CALLOUT_PROMPT = """Look at this exploded-view parts diagram from a motorcycle/ATV service catalog. List every callout number you can read in the image (numbers like 1, 2, 3, or sub-callouts like 1-1, 1-6L, 1-6R).

Return ONLY a JSON array of strings, no commentary. Example: ["1", "2", "3", "1-1", "1-6L"]

If you cannot identify any callout numbers, return []."""


class GeminiVisionProvider:
    """Vision provider backed by Google Gemini free tier."""

    name = "gemini-vision"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        api_base: str = DEFAULT_API_BASE,
        timeout_s: int = 60,
        max_retries: int = 5,
        min_interval_s: float = 0.0,
    ) -> None:
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self._model = model or os.environ.get("GEMINI_VISION_MODEL", DEFAULT_MODEL)
        self._api_base = api_base
        self._timeout = timeout_s
        self._max_retries = max_retries
        self._min_interval_s = min_interval_s
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
        url = f"{self._api_base}/{self._model}:generateContent"
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": _CALLOUT_PROMPT},
                        {"inline_data": {"mime_type": mime, "data": image_b64}},
                    ]
                }
            ],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 512},
        }

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
                    resp = await client.post(url, params={"key": self._api_key}, json=payload)
            except (httpx.RequestError, httpx.TimeoutException) as e:
                log.warning("Gemini vision call failed: %s", e)
                return self._fallback(req, reason="upstream_unreachable")

            if resp.status_code != 429:
                break

            retry_after = resp.headers.get("retry-after")
            wait_s = float(retry_after) if retry_after else 2.0 ** attempt
            wait_s = min(wait_s, 30.0)
            log.info(
                "Gemini 429 — sleeping %.1fs (attempt %d/%d)",
                wait_s, attempt, self._max_retries,
            )
            await asyncio.sleep(wait_s)

        latency_ms = int((time.monotonic() - start) * 1000)

        if resp is None or resp.status_code == 429:
            return self._fallback(req, reason="rate_limited_after_retry")
        if resp.status_code != 200:
            log.warning("Gemini HTTP %s: %s", resp.status_code, resp.text[:200])
            return self._fallback(req, reason=f"http_{resp.status_code}")

        data: Any = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return self._fallback(req, reason="empty_candidates")

        parts = candidates[0].get("content", {}).get("parts", [])
        raw_text = "".join(p.get("text", "") for p in parts)
        callouts = _parse_callouts(raw_text)
        usage = data.get("usageMetadata", {}) or {}

        return EnrichmentResponse(
            id=req.id,
            field=req.field,
            result=callouts,  # type: ignore[arg-type]
            confidence=_score_confidence(callouts),
            meta=EnrichmentMeta(
                provider=self.name,
                prompt_template_ver=PROMPT_TEMPLATE_VER,
                tokens_in=usage.get("promptTokenCount"),
                tokens_out=usage.get("candidatesTokenCount"),
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
        log.debug("Gemini vision fallback: %s", reason)
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
