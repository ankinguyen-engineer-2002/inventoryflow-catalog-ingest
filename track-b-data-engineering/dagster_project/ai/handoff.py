"""Claude-Code handoff provider — same JSONL file format as Track A."""

from __future__ import annotations

import json
from pathlib import Path

from .provider import (
    EnrichmentMeta,
    EnrichmentRequest,
    EnrichmentResponse,
)


class ClaudeCodeHandoffProvider:
    name = "claude-code-handoff"

    def __init__(self, tasks_file: Path, results_file: Path) -> None:
        self._tasks_file = Path(tasks_file)
        self._results_file = Path(results_file)
        self._tasks_file.parent.mkdir(parents=True, exist_ok=True)
        self._results_file.parent.mkdir(parents=True, exist_ok=True)
        self._results_cache: dict[str, dict] | None = None

    async def enrich(self, req: EnrichmentRequest) -> EnrichmentResponse:
        results = self._load_results()
        hit = results.get(req.id)
        if hit:
            return EnrichmentResponse(
                id=req.id,
                field=req.field,
                result=hit.get("result"),
                confidence=hit.get("confidence"),
                meta=EnrichmentMeta(
                    provider=self.name,
                    prompt_template_ver="handoff-v1",
                    cache_hit=False,
                ),
            )

        # Append task to handoff file for operator translation.
        with self._tasks_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "id": req.id,
                "field": req.field,
                "inputs": req.inputs,
            }) + "\n")

        return EnrichmentResponse(
            id=req.id,
            field=req.field,
            result=None,
            confidence="low",
            meta=EnrichmentMeta(
                provider=self.name,
                prompt_template_ver="handoff-v1",
                cache_hit=False,
            ),
        )

    def _load_results(self) -> dict[str, dict]:
        if self._results_cache is not None:
            return self._results_cache
        cache: dict[str, dict] = {}
        if self._results_file.exists():
            for line in self._results_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    cache[entry["id"]] = entry
                except (json.JSONDecodeError, KeyError):
                    continue
        self._results_cache = cache
        return cache
