"""LLM provider abstraction — Python port of Track A.

Mirrors the ILLMProvider interface and five concrete implementations
defined in track-a-jd-native/src/ai/. The cache file format (JSONL) is
identical so the same shared/llm-cache.jsonl serves both tracks.

Reusing the cache across tracks means the LLM seed work performed for
Track A is automatically available to Track B — no duplicate calls,
no duplicate spend.
"""

from .provider import (
    EnrichmentField,
    EnrichmentRequest,
    EnrichmentResponse,
    ILLMProvider,
)
from .factory import create_llm_provider

__all__ = [
    "EnrichmentField",
    "EnrichmentRequest",
    "EnrichmentResponse",
    "ILLMProvider",
    "create_llm_provider",
]
