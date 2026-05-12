"""Provider selection — driven by LLM_PROVIDER environment variable."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .cached import CachedLLMProvider
from .fallback_chain import FallbackChainProvider, Tier
from .groq_vision import GroqVisionProvider
from .handoff import ClaudeCodeHandoffProvider
from .mock import MockProvider
from .ollama import OllamaProvider
from .ollama_vision import OllamaVisionProvider
from .openrouter_vision import OpenRouterVisionProvider
from .provider import ILLMProvider
from .quota_tracker import QuotaTracker

log = logging.getLogger(__name__)


def create_llm_provider(
    *,
    cache_path: Path | str | None = None,
    handoff_dir: Path | str | None = None,
) -> ILLMProvider:
    """Instantiate the configured provider chain.

    Always wraps the chosen upstream in CachedLLMProvider so cache benefits
    apply regardless of which upstream is selected.

    Mirrors track-a-jd-native/src/ai/index.ts.
    """
    cache = Path(cache_path or os.environ.get("LLM_CACHE_PATH", "../shared/llm-cache.jsonl"))
    handoff = Path(handoff_dir or "../shared/handoff")

    provider_name = os.environ.get("LLM_PROVIDER", "cached").lower()
    upstream = _pick_upstream(provider_name, handoff)
    log.info("LLM provider initialised: upstream=%s cache=%s", upstream.name, cache)
    return CachedLLMProvider(upstream, cache)


def _pick_upstream(name: str, handoff_dir: Path) -> ILLMProvider:
    if name in ("mock", "cached"):
        return MockProvider()

    if name == "claude-code-handoff":
        return ClaudeCodeHandoffProvider(
            tasks_file=handoff_dir / "translation_tasks.jsonl",
            results_file=handoff_dir / "translation_results.jsonl",
        )

    if name == "ollama":
        return OllamaProvider(
            base_url=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
            model=os.environ.get("OLLAMA_MODEL", "qwen2.5:7b"),
        )

    if name == "ollama-vision":
        return OllamaVisionProvider(
            base_url=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
            model=os.environ.get("OLLAMA_VISION_MODEL", "qwen2.5vl:7b"),
        )

    if name == "groq-vision":
        return GroqVisionProvider(
            api_key=os.environ.get("GROQ_API_KEY"),
            model=os.environ.get(
                "GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"
            ),
        )

    if name == "openrouter-vision":
        return OpenRouterVisionProvider(
            api_key=os.environ.get("OPENROUTER_API_KEY"),
            model=os.environ.get(
                "OPENROUTER_VISION_MODEL", "baidu/qianfan-ocr-fast:free"
            ),
        )

    if name == "fallback-chain":
        # 4-tier production fallback chain — see ADR-007 v3 §Vision at scale.
        return _build_production_fallback_chain()

    if name == "anthropic":
        # Production target — documented but stubbed to Mock in this
        # submission to keep the reviewer experience cost-free.
        log.warning(
            "LLM_PROVIDER=anthropic stubbed to Mock; see ADR-007 for details"
        )
        return MockProvider()

    if name == "gemini":
        # Intentionally excluded: Gemini's free-tier TOS permits data
        # training, which is incompatible with production dealer data.
        log.warning("LLM_PROVIDER=gemini intentionally stubbed (TOS risk)")
        return MockProvider()

    return MockProvider()


def _build_production_fallback_chain() -> FallbackChainProvider:
    """4-tier production chain. Tier order = cheapest+most-specific first.

    Tier 1: OpenRouter qianfan-ocr-fast — OCR-specialist, free, 50 RPD
    Tier 2: Groq Llama-4 Scout 17B — general vision, free, 1000 RPD
    Tier 3: Ollama qwen2.5vl:7b — self-host, unlimited
    Tier 4: (commented) Anthropic Batch — paid 50%-off, last resort

    See ADR-007 v3 §Vision at scale for the full math + rationale.
    """
    quota_state = Path(
        os.environ.get("LLM_QUOTA_STATE", "~/.cache/inventoryflow/llm-quotas.json")
    ).expanduser()
    quota = QuotaTracker(quota_state)
    tiers: list[Tier] = []

    if os.environ.get("OPENROUTER_API_KEY"):
        tiers.append(
            Tier(
                provider=OpenRouterVisionProvider(min_interval_s=1.0),
                daily_limit=int(os.environ.get("OPENROUTER_DAILY_LIMIT", "50")),
                accept_confidence="medium",
                min_callouts=3,
                label="T1-openrouter-qianfan-ocr",
            )
        )

    if os.environ.get("GROQ_API_KEY"):
        tiers.append(
            Tier(
                provider=GroqVisionProvider(min_interval_s=5.0),
                daily_limit=int(os.environ.get("GROQ_DAILY_LIMIT", "1000")),
                accept_confidence="medium",
                min_callouts=3,
                label="T2-groq-llama4-scout",
            )
        )

    tiers.append(
        Tier(
            provider=OllamaVisionProvider(),
            daily_limit=999_999,  # effectively unlimited
            accept_confidence="medium",
            min_callouts=0,  # last automatic stop, accept anything non-null
            label="T3-ollama-qwen2.5vl-7b",
        )
    )

    # Tier 4 — Anthropic Batch (paid). Deferred: documented in ADR-007 v3
    # but not invoked by default to keep reviewer experience cost-free.

    log.info(
        "Fallback chain initialised with %d tiers: %s",
        len(tiers),
        [t.label for t in tiers],
    )
    return FallbackChainProvider(tiers, quota)
