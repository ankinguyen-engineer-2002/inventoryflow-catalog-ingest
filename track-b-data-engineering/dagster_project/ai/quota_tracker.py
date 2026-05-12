"""Per-provider daily quota tracker.

Persists usage to disk so quotas survive process restarts. Used by the
FallbackChainProvider to decide which free-tier provider to route the
next request to.

State file shape (JSON):
    {
        "groq-vision":          {"2026-05-12": 487},
        "openrouter-vision":    {"2026-05-12": 50},
        "ollama-vision":        {"2026-05-12": 13412},
    }

Old dates are pruned on every save (kept: today + yesterday only) so the
file stays small. This is a process-local persistence, not multi-host
coordination — for that, use Redis with TTL keys (see ADR-007 §Vision at
scale, "Production scale-out").
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)


class QuotaTracker:
    """File-backed daily quota counter, thread-safe within a process."""

    def __init__(self, state_file: Path | str) -> None:
        self._state_file = Path(state_file).expanduser()
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._usage: dict[str, dict[str, int]] = self._load()

    @staticmethod
    def _today() -> str:
        return date.today().isoformat()

    @staticmethod
    def _yesterday() -> str:
        return (date.today() - timedelta(days=1)).isoformat()

    def _load(self) -> dict[str, dict[str, int]]:
        if not self._state_file.exists():
            return {}
        try:
            return json.loads(self._state_file.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("Could not parse quota state at %s — starting fresh", self._state_file)
            return {}

    def _save(self) -> None:
        # Prune old dates — keep only today and yesterday.
        keep = {self._today(), self._yesterday()}
        pruned: dict[str, dict[str, int]] = {}
        for provider, by_date in self._usage.items():
            kept = {d: n for d, n in by_date.items() if d in keep}
            if kept:
                pruned[provider] = kept
        self._usage = pruned
        try:
            self._state_file.write_text(json.dumps(self._usage, indent=2))
        except OSError as exc:
            log.warning("Could not persist quota state: %s", exc)

    def used_today(self, provider: str) -> int:
        with self._lock:
            return self._usage.get(provider, {}).get(self._today(), 0)

    def remaining_today(self, provider: str, daily_limit: int) -> int:
        return max(0, daily_limit - self.used_today(provider))

    def can_use(self, provider: str, daily_limit: int) -> bool:
        return self.remaining_today(provider, daily_limit) > 0

    def increment(self, provider: str, n: int = 1) -> None:
        with self._lock:
            today = self._today()
            self._usage.setdefault(provider, {})[today] = (
                self._usage.get(provider, {}).get(today, 0) + n
            )
            self._save()

    def pick_provider_with_most_remaining(
        self, candidates: list[tuple[str, int]]
    ) -> str | None:
        """Pick the candidate with the largest remaining quota today.

        `candidates` is a list of (provider_name, daily_limit) tuples.
        Returns None if every candidate is exhausted.
        """
        best: tuple[str, int] | None = None
        for name, limit in candidates:
            remaining = self.remaining_today(name, limit)
            if remaining <= 0:
                continue
            if best is None or remaining > best[1]:
                best = (name, remaining)
        return best[0] if best else None

    def snapshot(self) -> dict[str, dict[str, int]]:
        """Snapshot for observability — e.g. Dagster asset metadata."""
        with self._lock:
            return {p: dict(by_date) for p, by_date in self._usage.items()}
