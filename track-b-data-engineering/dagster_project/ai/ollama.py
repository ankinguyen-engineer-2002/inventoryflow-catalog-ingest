"""Ollama provider — local LLM via HTTP. Same prompt template as Track A."""

from __future__ import annotations

import time
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

from .provider import (
    EnrichmentMeta,
    EnrichmentRequest,
    EnrichmentResponse,
)

PROMPT_TEMPLATE_VER = "ollama-translate-v1"

_TRANSLATE_PROMPT = """You are a translator for a powersports motorcycle/ATV parts catalog.

Translate this Chinese part name to natural English. Return ONLY the English translation,
no explanation, no quotes, no extra punctuation.

Chinese: {cn}

English:"""


class OllamaProvider:
    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "qwen2.5:7b") -> None:
        self._base_url = base_url
        self._model = model

    async def enrich(self, req: EnrichmentRequest) -> EnrichmentResponse:
        if httpx is None:
            return self._fallback(req, reason="httpx_not_installed")
        if req.field != "translate_cn_to_en":
            return self._fallback(req, reason="unsupported_field")

        cn = (req.inputs.get("cn") or "").strip()
        if not cn:
            return self._fallback(req, reason="empty_input")

        prompt = _TRANSLATE_PROMPT.format(cn=cn)
        start = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self._base_url}/api/generate",
                    json={
                        "model": self._model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0,
                            "top_p": 1,
                            "num_ctx": 512,
                            "num_predict": 64,
                        },
                    },
                )
        except (httpx.RequestError, httpx.TimeoutException):
            return self._fallback(req, reason="upstream_unreachable")

        latency_ms = int((time.monotonic() - start) * 1000)

        if resp.status_code != 200:
            return self._fallback(req, reason=f"http_{resp.status_code}")

        data: Any = resp.json()
        translated = _clean_translation(data.get("response", ""))
        confidence = _score_confidence(translated)

        return EnrichmentResponse(
            id=req.id,
            field=req.field,
            result=translated,
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

    def _fallback(self, req: EnrichmentRequest, *, reason: str) -> EnrichmentResponse:
        del reason  # accepted but unused; production builds log it
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


def _clean_translation(raw: str) -> str:
    """Strip common artefacts from local-LLM output."""
    s = raw.strip()
    for prefix in ("English:", "Translation:"):
        if s.lower().startswith(prefix.lower()):
            s = s[len(prefix):].strip()
    s = s.strip("\"'`")
    s = s.rstrip(".,;")
    return " ".join(s.split())


def _score_confidence(translated: str) -> str:
    if not translated:
        return "low"
    if translated.isascii():
        word_count = len(translated.split())
        if 2 <= word_count <= 6 or word_count == 1:
            return "medium"
        return "low"
    return "low"
