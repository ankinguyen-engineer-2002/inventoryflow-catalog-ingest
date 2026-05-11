#!/usr/bin/env node
/**
 * Enrichment CLI.
 *
 * Scans products with name_en IS NULL (or empty), translates name_cn → EN
 * via the configured ILLMProvider, and updates products.name_en plus
 * data_quality.translation_source.
 *
 * Usage:
 *   pnpm enrich                       # all rows missing name_en
 *   pnpm enrich --limit 100           # cap for testing
 *   pnpm enrich --dump-tasks          # write tasks.jsonl, don't update DB
 *                                       (use with LLM_PROVIDER=claude-code-handoff)
 *
 * Provider selection comes from LLM_PROVIDER env. The cached decorator
 * always wraps the upstream, so re-runs with hit cache are zero-cost.
 */
import { parseArgs } from "node:util";
import { createHash } from "node:crypto";
import { eq, isNull, sql as drizzleSql, or, and, ne } from "drizzle-orm";
import { db, closeDb, sql } from "../storage/db/client.js";
import { products } from "../storage/db/schema.js";
import { recordAudit } from "../storage/db/repositories/audit.repo.js";
import { createRun, finaliseRun } from "../storage/db/repositories/runs.repo.js";
import { createLLMProvider } from "../ai/index.js";
import { logger, withRun } from "../lib/logger.js";

interface CliOptions {
  limit: number | null;
  dumpTasks: boolean;
  mode: "fill" | "audit";
}

function parseCliArgs(): CliOptions {
  const { values } = parseArgs({
    options: {
      limit: { type: "string" },
      "dump-tasks": { type: "boolean", default: false },
      mode: { type: "string", default: "fill" }, // 'fill' | 'audit'
      help: { type: "boolean", default: false },
    },
  });

  if (values.help) {
    // eslint-disable-next-line no-console
    console.log(`
Usage:
  pnpm enrich [options]

Options:
  --limit <N>       Stop after enriching N rows.
  --mode <m>        'fill' (default) translates rows with name_en NULL.
                    'audit' cross-validates existing EN against fresh LLM
                    translation — produces translation_verified + a
                    consensus check (Layer 3 of accuracy guarantees).
  --dump-tasks      Generate tasks.jsonl for handoff translation, skip DB writes.
  --help            Show help.

Provider chosen via LLM_PROVIDER env.
`);
    process.exit(0);
  }

  const mode = values.mode === "audit" ? "audit" : "fill";
  return {
    limit: values.limit ? Number(values.limit) : null,
    dumpTasks: Boolean(values["dump-tasks"]),
    mode,
  };
}

async function main(): Promise<void> {
  const opts = parseCliArgs();
  const provider = createLLMProvider();

  const run = await createRun({
    sourceFile: "(enrichment)",
    sourceSha256: "n/a",
    status: "RUNNING",
  });
  const log = withRun(run.runId).child({ cli: "enrich", provider: provider.name });
  log.info({ opts }, "Enrichment run started");

  // Pull candidates by mode.
  //   fill  → rows where name_en is missing (typical CN→EN translation)
  //   audit → rows with EN already present; cross-validate via LLM
  //           (Layer 3 in our accuracy framework — same CN should yield
  //           consistent EN across providers; flags discrepancies into
  //           data_quality.translation_consensus)
  const candidatesQuery =
    opts.mode === "audit"
      ? db
          .select({
            id: products.id,
            partNumber: products.partNumber,
            nameCn: products.nameCn,
            nameEn: products.nameEn,
          })
          .from(products)
          .where(
            and(
              ne(products.nameCn, ""),
              drizzleSql`(data_quality->>'translation_verified') IS NULL`,
            ),
          )
          // Deterministic order so re-runs hit the same cache keys.
          .orderBy(products.id)
      : db
          .select({
            id: products.id,
            partNumber: products.partNumber,
            nameCn: products.nameCn,
            nameEn: products.nameEn,
          })
          .from(products)
          .where(
            and(
              or(isNull(products.nameEn), eq(products.nameEn, "")),
              ne(products.nameCn, ""),
              drizzleSql`(data_quality->>'translation_source') IS NULL`,
            ),
          );

  const candidates = opts.limit
    ? await candidatesQuery.limit(opts.limit)
    : await candidatesQuery;

  log.info({ candidates: candidates.length, mode: opts.mode }, "Candidates loaded");

  const counters = { attempted: 0, enriched: 0, skipped: 0, llmCalls: 0, llmCost: 0 };

  for (const c of candidates) {
    if (!c.nameCn || c.nameCn.trim() === "") {
      counters.skipped++;
      continue;
    }

    counters.attempted++;
    const id = `translate:${hashShort(c.nameCn)}`;
    const start = Date.now();

    const response = await provider.enrich({
      id,
      field: "translate_cn_to_en",
      inputs: { cn: c.nameCn },
    });

    const latency = Date.now() - start;
    counters.llmCalls++;
    counters.llmCost += response.meta.costUsd ?? 0;

    // Audit every call.
    await recordAudit({
      runId: run.runId,
      provider: response.meta.provider,
      promptSha256: createHash("sha256")
        .update(`translate_cn_to_en:${c.nameCn}`)
        .digest("hex"),
      promptTemplateVer: response.meta.promptTemplateVer,
      responseText: typeof response.result === "string" ? response.result : null,
      tokensIn: response.meta.tokensIn ?? null,
      tokensOut: response.meta.tokensOut ?? null,
      costUsd: response.meta.costUsd ?? null,
      latencyMs: response.meta.latencyMs ?? latency,
      cacheHit: response.meta.cacheHit ?? false,
    });

    if (opts.dumpTasks) {
      // Don't mutate the DB — just exercise the handoff write path.
      continue;
    }

    // Apply only when we got a usable string back.
    if (typeof response.result === "string" && response.result.trim()) {
      const llmEn = response.result.trim();

      if (opts.mode === "audit") {
        // Don't overwrite name_en — just record audit fields.
        // Consensus check: case-insensitive token overlap between current
        // name_en and LLM-fresh translation.
        const consensus = computeConsensus(c.nameEn ?? "", llmEn);
        // postgres.js gets confused by mixed booleans + numbers + strings
        // inside jsonb_build_object — pre-serialise the json patch.
        const patch = JSON.stringify({
          translation_verified: true,
          translation_verified_by: response.meta.provider,
          translation_consensus: consensus.label,
          translation_consensus_score: consensus.score,
          translation_llm_alt: llmEn,
          translation_template_ver: response.meta.promptTemplateVer,
          translation_confidence: response.confidence ?? "low",
        });
        await sql.unsafe(
          `UPDATE products
           SET data_quality = coalesce(data_quality, '{}'::jsonb) || $1::jsonb,
               updated_at = now()
           WHERE id = $2::bigint`,
          [patch, c.id],
        );
      } else {
        // fill mode: overwrite name_en
        await sql`
          UPDATE products
          SET name_en = ${llmEn},
              data_quality = coalesce(data_quality, '{}'::jsonb)
                             || jsonb_build_object(
                                  'translation_source', ${response.meta.provider},
                                  'translation_template_ver', ${response.meta.promptTemplateVer},
                                  'translation_cache_hit', ${response.meta.cacheHit ?? false},
                                  'translation_confidence', ${response.confidence ?? "low"}
                                ),
              updated_at = now()
          WHERE id = ${c.id}
        `;
      }
      counters.enriched++;
    }
  }

  await finaliseRun(run.runId, {
    status: counters.attempted === counters.enriched + counters.skipped ? "SUCCESS" : "PARTIAL",
    rowsAttempted: counters.attempted,
    rowsSucceeded: counters.enriched,
    rowsFailed: counters.attempted - counters.enriched - counters.skipped,
    llmCalls: counters.llmCalls,
    llmCostUsd: counters.llmCost,
  });

  log.info(counters, "Enrichment complete");
}

function hashShort(s: string): string {
  return createHash("sha256").update(s).digest("hex").slice(0, 16);
}

/**
 * Cheap consensus heuristic for translation cross-validation.
 *
 * Compares two English translations by lowercased token-set Jaccard.
 *   1.0 = identical, ≥0.5 = high overlap, ≥0.2 = some overlap, <0.2 = disagree
 *
 * Caller stores both label + raw score so downstream auditors can pick
 * thresholds independently.
 */
function computeConsensus(
  a: string,
  b: string,
): { label: "agree" | "partial" | "disagree"; score: number } {
  const tokA = new Set(a.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean));
  const tokB = new Set(b.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean));
  if (tokA.size === 0 && tokB.size === 0) return { label: "agree", score: 1 };
  if (tokA.size === 0 || tokB.size === 0) return { label: "disagree", score: 0 };
  let intersect = 0;
  for (const t of tokA) if (tokB.has(t)) intersect++;
  const union = tokA.size + tokB.size - intersect;
  const score = union === 0 ? 0 : intersect / union;
  if (score >= 0.5) return { label: "agree", score };
  if (score >= 0.2) return { label: "partial", score };
  return { label: "disagree", score };
}

main()
  .then(async () => {
    await closeDb();
    process.exit(0);
  })
  .catch(async (err: unknown) => {
    logger.error({ err }, "Enrich CLI failed");
    await closeDb().catch(() => {});
    process.exit(1);
  });
