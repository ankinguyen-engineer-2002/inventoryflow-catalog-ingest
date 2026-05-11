/**
 * Cached LLM provider — JSONL-backed decorator around any upstream.
 *
 * Cache key: SHA-256 over (field + inputs JSON + prompt_template_ver).
 * Cache hits return synchronously without calling upstream — that's how
 * the reviewer pays $0 to run the submission with the committed cache file.
 *
 * Storage format: JSON Lines (one cache entry per line). Chosen over
 * SQLite to avoid native dependencies (better-sqlite3 needs node-gyp;
 * node:sqlite is experimental + Vite can't bundle it). For our scale
 * (<10k cache entries) the perf difference is negligible.
 *
 * On boot, the file is fully loaded into a Map for O(1) lookup. New
 * entries are appended atomically (single fs.appendFile per write).
 */
import { createHash } from "node:crypto";
import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname } from "node:path";
import { logger } from "../../lib/logger.js";
import type {
  EnrichmentRequest,
  EnrichmentResponse,
  ILLMProvider,
} from "../provider.js";

interface CacheEntry {
  cache_key: string;
  provider: string;
  prompt_template_ver: string;
  response: EnrichmentResponse;
  created_at: string;
}

export class CachedLLMProvider implements ILLMProvider {
  readonly name: string;
  private readonly index = new Map<string, CacheEntry>();

  constructor(
    private readonly upstream: ILLMProvider,
    private readonly cachePath: string,
  ) {
    this.name = `cached:${upstream.name}`;
    mkdirSync(dirname(cachePath), { recursive: true });
    this.loadIndex();
  }

  async enrich(req: EnrichmentRequest): Promise<EnrichmentResponse> {
    const key = computeCacheKey(req);
    const hit = this.index.get(key);

    if (hit) {
      return {
        ...hit.response,
        meta: {
          ...hit.response.meta,
          cacheHit: true,
        },
      };
    }

    // Cache miss → upstream.
    const start = Date.now();
    const response = await this.upstream.enrich(req);
    const latency = Date.now() - start;

    const enriched: EnrichmentResponse = {
      ...response,
      meta: {
        ...response.meta,
        latencyMs: response.meta.latencyMs ?? latency,
        cacheHit: false,
      },
    };

    const entry: CacheEntry = {
      cache_key: key,
      provider: response.meta.provider,
      prompt_template_ver: response.meta.promptTemplateVer,
      response: enriched,
      created_at: new Date().toISOString(),
    };

    this.index.set(key, entry);
    try {
      appendFileSync(this.cachePath, JSON.stringify(entry) + "\n");
    } catch (err) {
      logger.warn({ err, key }, "cache write failed; continuing without cache");
    }

    return enriched;
  }

  close(): void {
    // No persistent handle to close for JSONL.
  }

  private loadIndex(): void {
    if (!existsSync(this.cachePath)) return;
    const content = readFileSync(this.cachePath, "utf8");
    for (const line of content.split("\n")) {
      if (!line.trim()) continue;
      try {
        const entry = JSON.parse(line) as CacheEntry;
        this.index.set(entry.cache_key, entry);
      } catch (err) {
        logger.warn({ err }, "skipping malformed cache line");
      }
    }
  }
}

function computeCacheKey(req: EnrichmentRequest): string {
  // Build a stable string by emitting inputs keys in sorted order.
  // (JSON.stringify's 2nd-arg replacer-array filters TOP-level keys
  // only, not nested — using it on the inputs subobject was a footgun
  // that previously collided every translate cache key.)
  const sortedInputs = sortObjectKeys(req.inputs);
  const payload = JSON.stringify({ field: req.field, inputs: sortedInputs });
  return createHash("sha256").update(payload).digest("hex");
}

function sortObjectKeys(obj: Record<string, unknown>): Record<string, unknown> {
  const sorted: Record<string, unknown> = {};
  for (const k of Object.keys(obj).sort()) sorted[k] = obj[k];
  return sorted;
}
