# Architecture Decision Records

> Every non-trivial architectural choice in this repo has an ADR here. The format includes an explicit **"AI suggestion vs my override"** section to make human judgment auditable.

## Index

| #  | Status     | Title                                                                         |
|----|------------|-------------------------------------------------------------------------------|
| 1  | Accepted   | [Two-track monorepo layout](ADR-001-two-track-monorepo.md)                    |
| 2  | Accepted   | [JSONB fitment vs normalized join table](ADR-002-jsonb-fitment.md)            |
| 3  | Accepted   | [SHA-256-keyed idempotent image upload](ADR-003-sha256-idempotent-images.md)  |
| 4  | Accepted   | [Drizzle over Prisma](ADR-004-drizzle-vs-prisma.md)                           |
| 5  | Accepted   | [Section detection via header regex (not row-index)](ADR-005-section-detection-strategy.md) |
| 6  | Accepted   | [Part number aliases for OEM rename history](ADR-006-part-number-aliases.md)  |
| 7  | Accepted   | [LLM provider cost strategy + zero-API-key submission](ADR-007-llm-provider-cost-strategy.md) |
| 8  | Accepted   | [Medallion architecture for Track B](ADR-008-medallion-architecture-track-b.md) |
| 9  | Accepted   | [Trigger criteria for migrating Track A → Track B](ADR-009-when-to-switch-tracks.md) |

## Template

```markdown
# ADR-NNN: <Title>

## Status
Accepted | Superseded by ADR-XXX | Deprecated

## Context
2–4 paragraphs: what is the problem, what are the constraints, what is at stake.

## Decision
One paragraph: what we are doing.

## AI suggestion vs my override
What Claude/Cursor initially proposed (paraphrased).
What I chose instead.
The concrete reason — link to benchmark, doc, or experiment.

## Trade-offs accepted
Bulleted list of what we lose. Be honest.

## When to revisit
A specific trigger condition (load, dealer count, error rate, etc.)

## Sources
- Doc links (with version + retrieval date)
- Prior art / blog references
- Internal benchmark notebooks
```

## Why ADRs?

Recruiter signal: a senior engineer using AI tooling must show **what they accepted vs overrode** from the LLM. ADRs make that auditable. A repo full of clean code with no ADRs reads like an LLM dump; a repo with rigorous ADRs reads like a senior who used the LLM as a tool.
