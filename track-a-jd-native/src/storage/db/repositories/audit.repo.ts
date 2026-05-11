/**
 * Audit repository.
 *
 * Records every LLM call (and other audited external API calls) into the
 * `ingest_audit` table. Indexed by run_id for cost roll-ups.
 */
import { db } from "../client.js";
import { ingestAudit } from "../schema.js";

export interface AuditEntry {
  runId: string;
  provider: string;
  promptSha256: string;
  promptTemplateVer: string;
  responseText: string | null;
  tokensIn: number | null;
  tokensOut: number | null;
  costUsd: number | null;
  latencyMs: number | null;
  cacheHit: boolean;
}

export async function recordAudit(entry: AuditEntry): Promise<void> {
  await db.insert(ingestAudit).values({
    runId: entry.runId,
    provider: entry.provider,
    promptSha256: entry.promptSha256,
    promptTemplateVer: entry.promptTemplateVer,
    responseText: entry.responseText,
    tokensIn: entry.tokensIn,
    tokensOut: entry.tokensOut,
    costUsd: entry.costUsd === null ? null : String(entry.costUsd),
    latencyMs: entry.latencyMs,
    cacheHit: entry.cacheHit,
  });
}
