# ADR-001: Two-track monorepo layout

## Status
Accepted — 2026-05-11

## Context

The test specifies a TypeScript + Node + PostgreSQL + Redis stack. My background is data engineering (Python + Polars + Spark + dbt). A single-track submission forces a choice:

- **Track-A-only**: Match the JD stack 1:1 but miss the chance to demonstrate that I understand its limits at scale.
- **Track-B-only**: Showcase scale-grade DE patterns but fail the stack-match expectation; reviewer may conclude I can't fit the team.

Either choice loses a signal a senior submission should carry. The test explicitly values "technical judgment over credentials" — and technical judgment is exactly what's invisible in a single-track choice.

## Decision

Ship both implementations in one monorepo, with `docs/COMPARISON.md` quantifying the trade-offs. Track A is the recommended answer for InventoryFlow's current stage; Track B is the documented migration target.

Repo layout:

```
inventoryflow-catalog-ingest/
├── track-a-jd-native/         ← TypeScript, runs end-to-end
├── track-b-data-engineering/  ← Python+Polars+Delta+dbt, runs PoC scope
├── shared/                    ← LLM cache, prompts, sample data refs
└── docs/                      ← Plan, ADRs, comparison, runbook
```

## AI suggestion vs my override

**Claude initially suggested**: a single TypeScript track to "stay focused on the JD" and "avoid splitting attention" — citing concern that Track B would dilute the submission.

**I overrode** because:

1. The JD explicitly weights "technical judgment" higher than credentials. A two-track approach is the most direct way to demonstrate judgment.
2. Track B is **proof of concept**, not production scope — bounded to 3 dbt models + 1 Prefect flow. Dilution risk is contained by tight scope.
3. Splitting attention is a real risk; mitigated by the timeline (Track B fits in Day 3 PM only, with Track A complete by Day 3 AM).
4. The reviewer can ignore Track B if they only want to evaluate stack fit — comparison doc is opt-in.

## Trade-offs accepted

- Repo is larger (~50 dirs vs ~25). README must point reviewer at the right entry.
- Code reuse between tracks requires careful module boundaries — only `shared/` and `ILLMProvider` cross.
- Track B's Python toolchain (Poetry, dbt) adds a second setup path for the reviewer. Mitigated: Track A runs standalone without Track B touched.
- Slight delivery-time pressure on Track B (3.5 hrs scope into one PM).

## When to revisit

- If timeline slips and Track B can't ship as PoC code, replace it with a Track-B-design memo only (no runnable code).
- If reviewer feedback says "this scope was excessive", future submissions go single-track.

## Sources

- Talemy Senior Engineer JD (2026-05-06) — "ownership, speed, technical judgment" emphasis.
- Test PDF (2026-05-08) — "Clean Architecture" criterion.
- Prior take-home submission patterns at staff-eng level (e.g., Stripe, Vercel) commonly include comparison documents.
