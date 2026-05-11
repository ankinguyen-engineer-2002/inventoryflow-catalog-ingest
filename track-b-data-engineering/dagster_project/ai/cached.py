"""Cached provider decorator — JSONL-backed, shared with Track A.

Reads track-a-jd-native/../shared/llm-cache.jsonl. Same cache key
algorithm so a cache entry written by Track A is found by Track B.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from .provider import (
    EnrichmentMeta,
    EnrichmentRequest,
    EnrichmentResponse,
    ILLMProvider,
)


class CachedLLMProvider:
    """Cache-then-call decorator. Identical key algorithm to Track A."""

    def __init__(self, upstream: ILLMProvider, cache_path: Path | str) -> None:
        self._upstream = upstream
        self._path = Path(cache_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, EnrichmentResponse] = {}
        self._load()

    @property
    def name(self) -> str:
        return f"cached:{self._upstream.name}"

    async def enrich(self, req: EnrichmentRequest) -> EnrichmentResponse:
        key = self._compute_key(req)
        hit = self._index.get(key)
        if hit:
            return EnrichmentResponse(
                id=hit.id,
                field=hit.field,
                result=hit.result,
                confidence=hit.confidence,
                meta=EnrichmentMeta(
                    provider=hit.meta.provider,
                    prompt_template_ver=hit.meta.prompt_template_ver,
                    tokens_in=hit.meta.tokens_in,
                    tokens_out=hit.meta.tokens_out,
                    cost_usd=hit.meta.cost_usd,
                    latency_ms=hit.meta.latency_ms,
                    cache_hit=True,
                ),
            )

        response = await self._upstream.enrich(req)

        # Never cache null results — they indicate "task pending" in the
        # handoff workflow and caching them would freeze the pipeline.
        if response.result is not None:
            self._index[key] = response
            try:
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "cache_key": key,
                        "provider": response.meta.provider,
                        "prompt_template_ver": response.meta.prompt_template_ver,
                        "response": asdict(response),
                    }) + "\n")
            except OSError:
                pass  # cache write failure is non-fatal

        return response

    def _load(self) -> None:
        if not self._path.exists():
            return
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                resp_dict = entry["response"]
                meta_dict = resp_dict["meta"]
                self._index[entry["cache_key"]] = EnrichmentResponse(
                    id=resp_dict["id"],
                    field=resp_dict["field"],
                    result=resp_dict["result"],
                    confidence=resp_dict.get("confidence"),
                    meta=EnrichmentMeta(**meta_dict),
                )
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    @staticmethod
    def _compute_key(req: EnrichmentRequest) -> str:
        sorted_inputs = {k: req.inputs[k] for k in sorted(req.inputs.keys())}
        payload = json.dumps(
            {"field": req.field, "inputs": sorted_inputs},
            separators=(",", ":"),
            sort_keys=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
