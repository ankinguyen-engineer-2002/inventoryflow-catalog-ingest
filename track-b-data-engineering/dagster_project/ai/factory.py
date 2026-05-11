"""Provider selection — driven by LLM_PROVIDER environment variable."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .cached import CachedLLMProvider
from .handoff import ClaudeCodeHandoffProvider
from .mock import MockProvider
from .ollama import OllamaProvider
from .provider import ILLMProvider


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
