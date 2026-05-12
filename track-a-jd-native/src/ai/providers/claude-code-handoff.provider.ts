/**
 * Claude Code handoff provider.
 *
 * Implements ADR-007's "zero-API-cost" strategy: the pipeline emits
 * pending tasks as JSON, blocks for an operator (me) to translate them
 * inside a Claude Code session (covered by my Claude Max subscription),
 * then reads back the results.
 *
 * In a normal (non-handoff) reviewer run, this provider is NOT used —
 * the cached provider answers from the committed SQLite cache and never
 * touches this code path.
 *
 * Behaviour on `enrich()`:
 *   1. Check the results file; if a result for this task id exists, return it.
 *   2. Otherwise, append the task to the tasks file and return a NEEDS_HANDOFF
 *      marker. The operator processes the tasks file in Claude Code and
 *      writes the corresponding results file before re-running the pipeline.
 *
 * This file/format-driven flow is why the cache can be committed and the
 * reviewer's run is bit-perfect reproducible.
 */
import { existsSync, readFileSync, appendFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";
import type {
  EnrichmentRequest,
  EnrichmentResponse,
  ILLMProvider,
} from "../provider.js";
import { logger } from "../../lib/logger.js";

const log = logger.child({ provider: "claude-code-handoff" });

export interface HandoffOptions {
  tasksFile: string;
  resultsFile: string;
}

interface ResultEntry {
  id: string;
  field: string;
  result: string | string[] | null;
  confidence?: "high" | "medium" | "low";
}

export class ClaudeCodeHandoffProvider implements ILLMProvider {
  readonly name = "claude-code-handoff";
  private resultsCache: Map<string, ResultEntry> | null = null;

  constructor(private readonly opts: HandoffOptions) {
    mkdirSync(dirname(opts.tasksFile), { recursive: true });
    mkdirSync(dirname(opts.resultsFile), { recursive: true });
  }

  async enrich(req: EnrichmentRequest): Promise<EnrichmentResponse> {
    const results = this.loadResults();
    const hit = results.get(req.id);
    if (hit) {
      return {
        id: req.id,
        field: req.field,
        result: hit.result,
        ...(hit.confidence ? { confidence: hit.confidence } : {}),
        meta: {
          provider: this.name,
          promptTemplateVer: "handoff-v1",
          cacheHit: false,
        },
      };
    }

    // Emit a task line for the operator.
    appendFileSync(
      this.opts.tasksFile,
      JSON.stringify({ id: req.id, field: req.field, inputs: req.inputs }) + "\n",
    );

    log.warn(
      { id: req.id, field: req.field, tasksFile: this.opts.tasksFile },
      "handoff required — task appended; pipeline will use null until results file updated",
    );

    return {
      id: req.id,
      field: req.field,
      result: null,
      confidence: "low",
      meta: {
        provider: this.name,
        promptTemplateVer: "handoff-v1",
        cacheHit: false,
      },
    };
  }

  private loadResults(): Map<string, ResultEntry> {
    if (this.resultsCache) return this.resultsCache;
    const cache = new Map<string, ResultEntry>();
    if (existsSync(this.opts.resultsFile)) {
      const lines = readFileSync(this.opts.resultsFile, "utf8")
        .split("\n")
        .filter((l) => l.trim());
      for (const line of lines) {
        try {
          const entry = JSON.parse(line) as ResultEntry;
          cache.set(entry.id, entry);
        } catch (err) {
          log.warn({ err, line }, "skipping malformed results line");
        }
      }
    }
    this.resultsCache = cache;
    return cache;
  }
}
