# Benchmarks

Empirical numbers backing claims in PLAN.md, COMPARISON.md, and ADRs.

## Planned benchmarks

| Bench                                  | Source                                                  | Output                                |
| -------------------------------------- | ------------------------------------------------------- | ------------------------------------- |
| Track A xlsx parse — streaming vs full | `track-a-jd-native/test/benchmark/xlsx-parse.bench.ts` | wall-time, peak RAM                   |
| Track A insert throughput              | `track-a-jd-native/test/benchmark/insert.bench.ts`     | rows/sec, by batch size               |
| Track A fitment query                  | `track-a-jd-native/test/benchmark/fitment-query.bench.ts` | p50/p95/p99 ms vs row count        |
| Drizzle vs Prisma emitted SQL          | `track-a-jd-native/test/benchmark/orm-comparison.ts`   | side-by-side queries + bundle size    |
| Track B Polars vs pandas               | `track-b-data-engineering/notebooks/polars-vs-pandas.ipynb` | wall-time on 241 MB file          |
| Track A vs Track B end-to-end          | (manual)                                                | wall-time, peak RAM, total cost       |

## Reporting format

Each benchmark output is a markdown file in this folder named `<bench-id>.md` with:

- Hardware spec (M2 Mac, 16 GB RAM, etc.)
- Date measured
- Versions of all dependencies
- Raw output (table or chart)
- Interpretation (one paragraph)

## Status

🚧 None measured yet. To be filled during Day 2 of the timeline.
