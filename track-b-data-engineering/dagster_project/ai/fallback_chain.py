"""Multi-tier fallback chain for vision (and other) LLM enrichment.

Architecture (full diagram in ADR-007 v3 §Vision at scale):

    request → Tier 0 (cache, handled by CachedLLMProvider outside this file)
            → Tier 1 free OCR specialist
            → Tier 2 free general vision (rotation across N providers)
            → Tier 3 self-host (Ollama)
            → Tier 4 paid batch (Anthropic Batch)

Each tier has:
  • a provider implementing ILLMProvider
  • a daily quota the QuotaTracker enforces
  • a "good enough" predicate — if not satisfied, escalate to next tier

Escalation can happen for two reasons:
  1. **Quota exhausted** — skip this tier today, try the next.
  2. **Low confidence** — tier ran but didn't return a useful result.

The chain never silently drops a request. If every tier fails (quota
exhausted + provider error), it returns the last response so the caller
can persist it and retry next day.

Note: this is NOT wrapped in CachedLLMProvider — wrap externally so the
cache check runs once, in front of the whole chain.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from .provider import EnrichmentRequest, EnrichmentResponse, ILLMProvider
from .quota_tracker import QuotaTracker

log = logging.getLogger(__name__)

Confidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class Tier:
    """One layer of the fallback chain."""

    provider: ILLMProvider
    daily_limit: int
    # A result passes when its confidence ≥ accept_confidence AND callout
    # count ≥ min_callouts. Set min_callouts=0 to accept any non-null result.
    accept_confidence: Confidence = "medium"
    min_callouts: int = 0
    # Human-readable description for logs / metadata.
    label: str = ""


_CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


class FallbackChainProvider:
    """Cycles requests through tiers, escalating on low confidence."""

    name = "fallback-chain"

    def __init__(self, tiers: list[Tier], quota: QuotaTracker) -> None:
        if not tiers:
            raise ValueError("FallbackChainProvider needs at least one tier")
        self._tiers = tiers
        self._quota = quota

    async def enrich(self, req: EnrichmentRequest) -> EnrichmentResponse:
        last_response: EnrichmentResponse | None = None
        for i, tier in enumerate(self._tiers):
            name = tier.provider.name
            label = tier.label or f"tier{i}"

            if not self._quota.can_use(name, tier.daily_limit):
                log.info(
                    "[chain] %s quota exhausted today (%d used of %d) — skip",
                    label,
                    self._quota.used_today(name),
                    tier.daily_limit,
                )
                continue

            log.info("[chain] trying %s (%s)", label, name)
            response = await tier.provider.enrich(req)

            if response.result is None:
                log.info("[chain] %s returned null — escalate", label)
                last_response = response
                continue

            # We count the call against quota only when the upstream actually
            # ran (response.result is not None and not a cache hit).
            if not response.meta.cache_hit:
                self._quota.increment(name)

            if self._is_acceptable(response, tier):
                log.info(
                    "[chain] %s accepted (callouts=%d confidence=%s)",
                    label,
                    self._count_callouts(response),
                    response.confidence,
                )
                return response

            log.info(
                "[chain] %s under threshold (callouts=%d confidence=%s) — escalate",
                label,
                self._count_callouts(response),
                response.confidence,
            )
            last_response = response

        # All tiers exhausted. Return last response — caller can decide what
        # to do (skip-cache, log to DLQ, retry tomorrow when quotas reset).
        if last_response is not None:
            log.warning(
                "[chain] all %d tiers exhausted for req %s — returning last response",
                len(self._tiers),
                req.id,
            )
            return last_response

        # No tier even tried (everyone quota-exhausted). Synthesize empty.
        from .provider import EnrichmentMeta

        log.warning(
            "[chain] every tier quota-exhausted for req %s — returning empty",
            req.id,
        )
        return EnrichmentResponse(
            id=req.id,
            field=req.field,
            result=None,
            confidence="low",
            meta=EnrichmentMeta(
                provider=self.name,
                prompt_template_ver="fallback-empty-v1",
                cache_hit=False,
            ),
        )

    @staticmethod
    def _count_callouts(resp: EnrichmentResponse) -> int:
        if isinstance(resp.result, list):
            return len(resp.result)
        return 0

    @staticmethod
    def _is_acceptable(resp: EnrichmentResponse, tier: Tier) -> bool:
        actual = _CONFIDENCE_ORDER.get(resp.confidence or "low", 0)
        wanted = _CONFIDENCE_ORDER[tier.accept_confidence]
        if actual < wanted:
            return False
        callouts = FallbackChainProvider._count_callouts(resp)
        return callouts >= tier.min_callouts
