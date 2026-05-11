# Changelog

Notable changes to this submission. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Added — 2026-05-11 (Day 0)

- Initial repo scaffold and PLAN.md
- Two-track monorepo structure (`track-a-jd-native/`, `track-b-data-engineering/`)
- 9 ADRs covering all major design decisions
- `docs/COMPARISON.md` — 16-dimension trade-off matrix
- `docs/QUESTIONS_FOR_RECRUITER.md` — open questions + assumptions + signals
- `docs/runbook.md` — operational reference
- `.gitignore`, `.env.example` (both tracks), `CHANGELOG.md`
- README files (root + per-track)

### Pending — Day 1

- `track-a-jd-native/src/ingest/*` (xlsx-reader, section-detector, drawing-parser)
- `track-a-jd-native/src/storage/db/schema.ts` (Drizzle schema)
- `track-a-jd-native/docker-compose.yml`
- First migration

### Pending — Day 2

- BullMQ workers, R2 uploader, full-file ingest run

### Pending — Day 3

- `ILLMProvider` + 5 providers + SQLite cache
- Track B PoC (Polars + Delta + dbt skeleton)

### Pending — Day 4

- Final README pass, submission
