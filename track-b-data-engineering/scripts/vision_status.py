"""Print cache status — how many images processed, by which provider.

Quick health check before running the daily rotation:

    python3 scripts/vision_status.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

CACHE_PATH = Path(__file__).resolve().parent.parent.parent / "shared" / "llm-cache.jsonl"


def main() -> int:
    if not CACHE_PATH.exists():
        print(f"Cache file not found: {CACHE_PATH}")  # noqa: T201
        return 1

    total_target = 1586
    by_provider: Counter[str] = Counter()
    with_callouts = 0
    null_results = 0
    seen_sha: set[str] = set()

    with CACHE_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            resp = entry.get("response", {})
            if resp.get("field") != "extract_callouts":
                continue
            req_id = resp.get("id", "")
            sha = req_id.replace("vision:", "")
            if sha in seen_sha:
                continue
            seen_sha.add(sha)

            provider = resp.get("meta", {}).get("provider", "unknown")
            by_provider[provider] += 1

            result = resp.get("result")
            if result is None:
                null_results += 1
            elif isinstance(result, list) and len(result) > 0:
                with_callouts += 1

    total = len(seen_sha)
    remaining = total_target - total
    coverage_pct = 100 * total / total_target if total_target else 0
    callout_pct = 100 * with_callouts / total if total else 0

    print("=" * 60)  # noqa: T201
    print("  Vision cache status")  # noqa: T201
    print("=" * 60)  # noqa: T201
    print(f"  Total unique images processed : {total} / {total_target} ({coverage_pct:.1f}%)")  # noqa: T201
    print(f"  Remaining to process          : {remaining}")  # noqa: T201
    print(f"  Images with real callouts     : {with_callouts} ({callout_pct:.1f}% of processed)")  # noqa: T201
    print(f"  Null results (transient err)  : {null_results}")  # noqa: T201
    print("")  # noqa: T201
    print("  By provider:")  # noqa: T201
    for provider, count in by_provider.most_common():
        print(f"    {provider:30s} {count:>5}")  # noqa: T201
    print("")  # noqa: T201

    if remaining > 0:
        days_at_1k = max(1, (remaining + 999) // 1000)
        print(f"  → Estimated days to 100% @ Groq 1k RPD: {days_at_1k}")  # noqa: T201
        print("  → Run: ./scripts/vision_daily_run.sh")  # noqa: T201
    else:
        print("  ✅ Full coverage reached. Time to commit.")  # noqa: T201
    print("")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
