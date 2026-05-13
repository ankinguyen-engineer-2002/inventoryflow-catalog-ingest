# Benchmarks

Empirical numbers backing claims in PLAN.md, COMPARISON.md, and ADRs.

## Status

✅ **Fitment-query latency benchmark measured.** Output: [`bench-results.json`](./bench-results.json) (Track A) and [`track-b-bench-results.json`](./track-b-bench-results.json) (Track B). Re-runnable via `pnpm bench` (Track A) and `make track-b-query` (Track B).

## Track A — fitment query latency

Measured on Node 22.22.2, M2 Mac, 1000 query samples on 3,938 products with the production `GIN jsonb_path_ops` index on `products.fitment`.

| p50 | p95 | p99 | max | Index size |
|---|---|---|---|---|
| 0.61 ms | 0.83 ms | **0.99 ms** | 2.12 ms | 760 KB |

Query shape:

```sql
SELECT part_number, name_en
FROM products
WHERE fitment @> '[{"make":"Kayo","model_code":"AT125-B"}]'
LIMIT 10;
```

To re-run:

```bash
cd track-a-jd-native
pnpm bench
```

## Track B — DuckDB-on-Iceberg fitment query

Same conceptual query against the Iceberg `gold_products_mart` table via DuckDB-on-Iceberg.

| p50 | p95 | p99 | max |
|---|---|---|---|
| 4.287 ms | 5.021 ms | 5.618 ms | 14.217 ms |

To re-run:

```bash
cd track-b-data-engineering
make track-b-query
```

## Comparison

Track A serves the hot-path lookup ~5× faster than Track B at this scale, which is the expected trade-off: Postgres + GIN beats DuckDB-on-Iceberg for point-lookup containment, while Iceberg wins on time-travel, schema evolution, and cross-table analytics. See [`COMPARISON.md`](../COMPARISON.md) for the 18-dimension analysis.

## Planned future benchmarks (not yet measured)

| Bench | Source | Output |
|---|---|---|
| Track A xlsx parse — streaming vs full | `track-a-jd-native/test/benchmark/xlsx-parse.bench.ts` | wall-time, peak RAM |
| Track A insert throughput | `track-a-jd-native/test/benchmark/insert.bench.ts` | rows/sec, by batch size |
| Drizzle vs Prisma emitted SQL | `track-a-jd-native/test/benchmark/orm-comparison.ts` | side-by-side queries + bundle size |
| Track B Polars vs pandas | `track-b-data-engineering/notebooks/polars-vs-pandas.ipynb` | wall-time on 241 MB file |
| Track A vs Track B end-to-end | (manual) | wall-time, peak RAM, total cost |

## Reporting format

Each measured benchmark output is a markdown file in this folder named `<bench-id>.md` with:

- Hardware spec (M2 Mac, 16 GB RAM, etc.)
- Date measured
- Versions of all dependencies
- Raw output (table or chart)
- Interpretation (one paragraph)
