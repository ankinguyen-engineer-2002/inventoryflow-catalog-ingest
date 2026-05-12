"""Unit tests for the FallbackChainProvider + QuotaTracker.

These tests use fake providers to isolate the chain logic from any real
LLM HTTP calls. The fakes record which tiers were called so we can
assert escalation paths.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from dagster_project.ai.fallback_chain import FallbackChainProvider, Tier
from dagster_project.ai.provider import (
    EnrichmentMeta,
    EnrichmentRequest,
    EnrichmentResponse,
)
from dagster_project.ai.quota_tracker import QuotaTracker


class _FakeProvider:
    """Records calls and returns a canned response."""

    def __init__(self, name: str, result, confidence: str = "high") -> None:
        self._name = name
        self.calls = 0
        self._result = result
        self._confidence = confidence

    @property
    def name(self) -> str:
        return self._name

    async def enrich(self, req: EnrichmentRequest) -> EnrichmentResponse:
        self.calls += 1
        return EnrichmentResponse(
            id=req.id,
            field=req.field,
            result=self._result,
            confidence=self._confidence,  # type: ignore[arg-type]
            meta=EnrichmentMeta(
                provider=self._name,
                prompt_template_ver="test-v1",
                cache_hit=False,
            ),
        )


def _req() -> EnrichmentRequest:
    return EnrichmentRequest(
        id="t1", field="extract_callouts", inputs={"image_sha256": "abc"}
    )


def _quota(tmp_path: Path) -> QuotaTracker:
    return QuotaTracker(tmp_path / "q.json")


# ─────────────────────────────────────────────────────────────────────────
# Test 1: first tier accepts → other tiers not called.
# ─────────────────────────────────────────────────────────────────────────
def test_first_tier_accepts(tmp_path: Path) -> None:
    t1 = _FakeProvider("t1", ["1", "2", "3", "4", "5"], confidence="high")
    t2 = _FakeProvider("t2", ["1", "2", "3"])
    chain = FallbackChainProvider(
        tiers=[
            Tier(provider=t1, daily_limit=100, accept_confidence="medium", min_callouts=3),
            Tier(provider=t2, daily_limit=100, accept_confidence="medium", min_callouts=3),
        ],
        quota=_quota(tmp_path),
    )
    resp = asyncio.run(chain.enrich(_req()))
    assert resp.result == ["1", "2", "3", "4", "5"]
    assert t1.calls == 1
    assert t2.calls == 0


# ─────────────────────────────────────────────────────────────────────────
# Test 2: low-confidence tier 1 → escalate to tier 2 → accepted.
# ─────────────────────────────────────────────────────────────────────────
def test_low_confidence_escalates(tmp_path: Path) -> None:
    t1 = _FakeProvider("t1", ["1"], confidence="low")
    t2 = _FakeProvider("t2", ["1", "2", "3", "4", "5"], confidence="high")
    chain = FallbackChainProvider(
        tiers=[
            Tier(provider=t1, daily_limit=100, accept_confidence="medium", min_callouts=3),
            Tier(provider=t2, daily_limit=100, accept_confidence="medium", min_callouts=3),
        ],
        quota=_quota(tmp_path),
    )
    resp = asyncio.run(chain.enrich(_req()))
    assert resp.result == ["1", "2", "3", "4", "5"]
    assert resp.meta.provider == "t2"
    assert t1.calls == 1
    assert t2.calls == 1


# ─────────────────────────────────────────────────────────────────────────
# Test 3: tier with exhausted quota skipped before being called.
# ─────────────────────────────────────────────────────────────────────────
def test_quota_exhausted_skipped(tmp_path: Path) -> None:
    t1 = _FakeProvider("t1", ["1", "2", "3"])
    t2 = _FakeProvider("t2", ["1", "2", "3"], confidence="high")
    quota = _quota(tmp_path)
    quota.increment("t1", 100)  # exhaust tier 1 quota
    chain = FallbackChainProvider(
        tiers=[
            Tier(provider=t1, daily_limit=100, min_callouts=3),
            Tier(provider=t2, daily_limit=100, min_callouts=3),
        ],
        quota=quota,
    )
    resp = asyncio.run(chain.enrich(_req()))
    assert t1.calls == 0  # never called — quota exhausted
    assert t2.calls == 1
    assert resp.meta.provider == "t2"


# ─────────────────────────────────────────────────────────────────────────
# Test 4: every tier exhausted → returns empty response.
# ─────────────────────────────────────────────────────────────────────────
def test_all_tiers_exhausted(tmp_path: Path) -> None:
    t1 = _FakeProvider("t1", ["1"])
    t2 = _FakeProvider("t2", ["1"])
    quota = _quota(tmp_path)
    quota.increment("t1", 10)
    quota.increment("t2", 10)
    chain = FallbackChainProvider(
        tiers=[
            Tier(provider=t1, daily_limit=10),
            Tier(provider=t2, daily_limit=10),
        ],
        quota=quota,
    )
    resp = asyncio.run(chain.enrich(_req()))
    assert resp.result is None
    assert resp.meta.provider == "fallback-chain"
    assert t1.calls == 0
    assert t2.calls == 0


# ─────────────────────────────────────────────────────────────────────────
# Test 5: null result (transient error) → escalate to next tier.
# ─────────────────────────────────────────────────────────────────────────
def test_null_result_escalates(tmp_path: Path) -> None:
    t1 = _FakeProvider("t1", None, confidence="low")
    t2 = _FakeProvider("t2", ["1", "2", "3"], confidence="high")
    chain = FallbackChainProvider(
        tiers=[
            Tier(provider=t1, daily_limit=100, min_callouts=3),
            Tier(provider=t2, daily_limit=100, min_callouts=3),
        ],
        quota=_quota(tmp_path),
    )
    resp = asyncio.run(chain.enrich(_req()))
    assert resp.result == ["1", "2", "3"]
    assert t1.calls == 1
    assert t2.calls == 1


# ─────────────────────────────────────────────────────────────────────────
# Test 6: quota increments only when upstream actually ran.
# ─────────────────────────────────────────────────────────────────────────
def test_quota_increment_accounting(tmp_path: Path) -> None:
    t1 = _FakeProvider("t1", ["1", "2", "3", "4", "5"], confidence="high")
    quota = _quota(tmp_path)
    chain = FallbackChainProvider(
        tiers=[Tier(provider=t1, daily_limit=10, min_callouts=3)],
        quota=quota,
    )
    asyncio.run(chain.enrich(_req()))
    asyncio.run(chain.enrich(_req()))
    asyncio.run(chain.enrich(_req()))
    assert quota.used_today("t1") == 3
    assert quota.remaining_today("t1", 10) == 7


# ─────────────────────────────────────────────────────────────────────────
# Test 7: QuotaTracker persists state across instantiations.
# ─────────────────────────────────────────────────────────────────────────
def test_quota_persistence(tmp_path: Path) -> None:
    state = tmp_path / "q.json"
    q1 = QuotaTracker(state)
    q1.increment("foo", 5)
    q2 = QuotaTracker(state)
    assert q2.used_today("foo") == 5


# ─────────────────────────────────────────────────────────────────────────
# Test 8: pick_provider_with_most_remaining picks correctly.
# ─────────────────────────────────────────────────────────────────────────
def test_pick_least_used(tmp_path: Path) -> None:
    q = QuotaTracker(tmp_path / "q.json")
    q.increment("a", 80)
    q.increment("b", 20)
    q.increment("c", 50)
    picked = q.pick_provider_with_most_remaining(
        [("a", 100), ("b", 100), ("c", 100)]
    )
    assert picked == "b"  # least used → most remaining


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
