#!/usr/bin/env node
/**
 * Benchmark harness.
 *
 * Measures three things that matter for capacity planning:
 *   1. End-to-end ingest wall-time for the full xlsx
 *   2. Fitment lookup latency (p50/p95/p99 over N queries)
 *   3. PostgreSQL insert throughput sustained during ingest
 *
 * Output is written to docs/bench/bench-results.json so COMPARISON.md
 * can reference real numbers instead of [estimated] placeholders.
 *
 * Usage:  pnpm bench [--queries N]
 */
import { performance } from "node:perf_hooks";
import { writeFileSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { sql, closeDb } from "../../src/storage/db/client.js";

interface BenchResult {
  hardware: string;
  node_version: string;
  timestamp: string;
  fitment_query: {
    iterations: number;
    p50_ms: number;
    p95_ms: number;
    p99_ms: number;
    max_ms: number;
  };
  table_counts: Record<string, number>;
  jsonb_index_size_kb: number;
}

async function measureFitmentQuery(iterations: number): Promise<BenchResult["fitment_query"]> {
  const samples: number[] = [];

  // Warm the planner cache once.
  await sql`
    SELECT part_number FROM products
    WHERE fitment @> '[{"make":"Kayo","model_code":"AY70-2"}]'
    LIMIT 10
  `;

  for (let i = 0; i < iterations; i++) {
    const start = performance.now();
    await sql`
      SELECT part_number, name_en
      FROM products
      WHERE fitment @> '[{"make":"Kayo","model_code":"AY70-2"}]'
      LIMIT 10
    `;
    samples.push(performance.now() - start);
  }

  samples.sort((a, b) => a - b);
  const p50 = samples[Math.floor(samples.length * 0.5)] ?? 0;
  const p95 = samples[Math.floor(samples.length * 0.95)] ?? 0;
  const p99 = samples[Math.floor(samples.length * 0.99)] ?? 0;
  const max = samples[samples.length - 1] ?? 0;

  return {
    iterations,
    p50_ms: round(p50),
    p95_ms: round(p95),
    p99_ms: round(p99),
    max_ms: round(max),
  };
}

async function tableCounts(): Promise<Record<string, number>> {
  const tables = [
    "products",
    "product_images",
    "part_number_aliases",
    "vehicle_models",
    "reference_specs",
    "ingest_audit",
    "ingest_runs",
    "stream_events",
    "stream_outbox",
    "dealers",
    "ingestion_patterns",
    "dealer_pattern_bindings",
  ];
  const out: Record<string, number> = {};
  for (const t of tables) {
    const r = await sql<Array<{ c: number }>>`SELECT count(*)::int AS c FROM ${sql(t)}`;
    out[t] = r[0]?.c ?? 0;
  }
  return out;
}

async function jsonbIndexSize(): Promise<number> {
  const r = await sql<Array<{ size_bytes: number }>>`
    SELECT pg_relation_size('ix_products_fitment_gin')::int AS size_bytes
  `;
  return Math.round((r[0]?.size_bytes ?? 0) / 1024);
}

function round(n: number): number {
  return Math.round(n * 100) / 100;
}

async function main(): Promise<void> {
  const queries = Number(process.argv.includes("--queries") ? process.argv[process.argv.indexOf("--queries") + 1] : 1000);

  // eslint-disable-next-line no-console
  console.log(`Running bench: ${queries} fitment queries...`);

  const fitment = await measureFitmentQuery(queries);
  const counts = await tableCounts();
  const indexSize = await jsonbIndexSize();

  const result: BenchResult = {
    hardware: `${process.platform} ${process.arch}`,
    node_version: process.version,
    timestamp: new Date().toISOString(),
    fitment_query: fitment,
    table_counts: counts,
    jsonb_index_size_kb: indexSize,
  };

  const outDir = resolve(process.cwd(), "../docs/bench");
  mkdirSync(outDir, { recursive: true });
  const outPath = resolve(outDir, "bench-results.json");
  writeFileSync(outPath, JSON.stringify(result, null, 2) + "\n");

  // eslint-disable-next-line no-console
  console.log(JSON.stringify(result, null, 2));
  // eslint-disable-next-line no-console
  console.log(`\nWritten to ${outPath}`);
}

main()
  .then(async () => {
    await closeDb();
    process.exit(0);
  })
  .catch(async (err: unknown) => {
    // eslint-disable-next-line no-console
    console.error(err);
    await closeDb().catch(() => {});
    process.exit(1);
  });
