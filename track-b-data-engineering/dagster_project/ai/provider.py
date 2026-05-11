"""ILLMProvider interface — mirrors track-a-jd-native/src/ai/provider.ts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

EnrichmentField = Literal[
    "translate_cn_to_en",
    "extract_callouts",
    "infer_make",
]


@dataclass(frozen=True)
class EnrichmentRequest:
    id: str
    field: EnrichmentField
    inputs: dict[str, str | None]


@dataclass(frozen=True)
class EnrichmentMeta:
    provider: str
    prompt_template_ver: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None
    cache_hit: bool = False


@dataclass(frozen=True)
class EnrichmentResponse:
    id: str
    field: EnrichmentField
    result: str | list[int] | None
    meta: EnrichmentMeta
    confidence: Literal["high", "medium", "low"] | None = None


class ILLMProvider(Protocol):
    """Single interface every provider implements."""

    @property
    def name(self) -> str: ...

    async def enrich(self, req: EnrichmentRequest) -> EnrichmentResponse: ...
