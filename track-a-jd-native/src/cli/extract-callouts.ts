/**
 * `pnpm extract-callouts` — populate the `image_callouts` table from the
 * shared LLM cache.
 *
 * The Vision LLM run is performed offline by
 * `track-b-data-engineering/scripts/vision_extract_all.py`. That script
 * calls Groq Cloud (free-tier Llama-4 Scout 17B) or local Ollama
 * qwen2.5vl:7b and writes per-image callout extractions to
 * `shared/llm-cache.jsonl`.
 *
 * This CLI replays the cache into Track A's PostgreSQL `image_callouts`
 * table so the catalog API can serve `callouts` JSONB alongside the
 * existing fitment data, with zero further LLM cost.
 */
import { createReadStream } from "node:fs";
import { resolve } from "node:path";
import { createInterface } from "node:readline";
import { db, closeDb } from "../storage/db/client.js";
import { imageCallouts } from "../storage/db/schema.js";
import { logger } from "../lib/logger.js";

interface CacheEntry {
  cache_key: string;
  provider: string;
  prompt_template_ver: string;
  response: {
    id: string;
    field: string;
    result: string[] | null;
    confidence: "high" | "medium" | "low";
    meta: { provider: string; cacheHit?: boolean; cache_hit?: boolean };
  };
}

interface CalloutRow {
  imageSha256: string;
  callouts: string[];
  calloutCount: number;
  confidence: "high" | "medium" | "low";
  visionProvider: string;
  cacheHit: boolean;
}

async function main(): Promise<void> {
  const cachePath = resolve(process.env.LLM_CACHE_PATH ?? "../shared/llm-cache.jsonl");
  logger.info({ cachePath }, "Reading shared LLM cache");

  const rows = await loadVisionEntries(cachePath);
  logger.info({ count: rows.length }, "Vision entries found in cache");

  if (rows.length === 0) {
    logger.warn("No vision entries in cache. Run vision_extract_all.py first.");
    process.exit(0);
  }

  // Bulk UPSERT in batches of 200 to stay under postgres parameter limits.
  const BATCH = 200;
  let upserted = 0;
  for (let i = 0; i < rows.length; i += BATCH) {
    const chunk = rows.slice(i, i + BATCH);
    await db
      .insert(imageCallouts)
      .values(
        chunk.map((r) => ({
          imageSha256: r.imageSha256,
          callouts: r.callouts,
          calloutCount: r.calloutCount,
          confidence: r.confidence,
          visionProvider: r.visionProvider,
          cacheHit: r.cacheHit,
          sourceSheets: [],
          imageSizeBytes: null,
        })),
      )
      .onConflictDoUpdate({
        target: imageCallouts.imageSha256,
        set: {
          callouts: sqlExcluded("callouts"),
          calloutCount: sqlExcluded("callout_count"),
          confidence: sqlExcluded("confidence"),
          visionProvider: sqlExcluded("vision_provider"),
          cacheHit: sqlExcluded("cache_hit"),
        },
      });
    upserted += chunk.length;
    if (upserted % 500 === 0 || upserted === rows.length) {
      logger.info({ upserted, total: rows.length }, "UPSERT progress");
    }
  }

  // Headline numbers
  const withCallouts = rows.filter((r) => r.calloutCount > 0).length;
  const providers: Record<string, number> = {};
  for (const r of rows) providers[r.visionProvider] = (providers[r.visionProvider] ?? 0) + 1;

  logger.info(
    {
      total_rows: rows.length,
      images_with_callouts: withCallouts,
      coverage_pct: ((100 * withCallouts) / rows.length).toFixed(1),
      providers,
    },
    "image_callouts populated",
  );
}

/** Parse the JSONL cache, keep only extract_callouts entries, dedupe by image SHA. */
async function loadVisionEntries(path: string): Promise<CalloutRow[]> {
  const stream = createInterface({ input: createReadStream(path, "utf-8"), crlfDelay: Infinity });
  const seen = new Map<string, CalloutRow>();

  for await (const line of stream) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    let entry: CacheEntry;
    try {
      entry = JSON.parse(trimmed) as CacheEntry;
    } catch {
      continue;
    }
    if (entry.response?.field !== "extract_callouts") continue;
    const sha = extractSha(entry.response.id);
    if (!sha) continue;
    const result = entry.response.result;
    if (result === null) continue;
    const callouts = Array.isArray(result) ? result.map(String) : [];

    seen.set(sha, {
      imageSha256: sha,
      callouts,
      calloutCount: callouts.length,
      confidence: entry.response.confidence ?? "low",
      visionProvider: entry.response.meta?.provider ?? "unknown",
      cacheHit: Boolean(entry.response.meta?.cacheHit ?? entry.response.meta?.cache_hit),
    });
  }
  return [...seen.values()];
}

function extractSha(id: string | undefined): string | null {
  if (!id) return null;
  // ids look like "vision:0005c5e10805..."
  const m = /^vision:([0-9a-f]{64})$/i.exec(id);
  return m ? m[1]! : null;
}

import { sql as drizzleSql } from "drizzle-orm";
function sqlExcluded(col: string): ReturnType<typeof drizzleSql.raw> {
  return drizzleSql.raw(`EXCLUDED.${col}`);
}

main()
  .then(() => closeDb())
  .then(() => process.exit(0))
  .catch((err) => {
    logger.error({ err }, "extract-callouts failed");
    closeDb().finally(() => process.exit(1));
  });
