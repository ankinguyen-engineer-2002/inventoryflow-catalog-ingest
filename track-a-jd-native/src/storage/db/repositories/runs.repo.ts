/**
 * Ingest run repository.
 *
 * Owns the lifecycle of `ingest_runs` rows: CREATE on dispatch, UPDATE on
 * status transitions, finalise on completion. Every run gets a UUID
 * (DB-generated) so callers can correlate logs.
 */
import { eq, sql as drizzleSql } from "drizzle-orm";
import { db } from "../client.js";
import { ingestRuns, type IngestRun, type NewIngestRun } from "../schema.js";

export type RunStatus =
  | "QUEUED"
  | "RUNNING"
  | "SUCCESS"
  | "PARTIAL"
  | "FAILED"
  | "SKIPPED";

export interface RunFinalisePatch {
  status: RunStatus;
  rowsAttempted?: number;
  rowsSucceeded?: number;
  rowsFailed?: number;
  llmCalls?: number;
  llmCostUsd?: number;
  error?: string;
  reason?: string;
}

export async function createRun(input: NewIngestRun): Promise<IngestRun> {
  const [row] = await db.insert(ingestRuns).values(input).returning();
  if (!row) throw new Error("createRun: insert returned no row");
  return row;
}

export async function updateRunStatus(runId: string, status: RunStatus): Promise<void> {
  await db.update(ingestRuns).set({ status }).where(eq(ingestRuns.runId, runId));
}

export async function finaliseRun(runId: string, patch: RunFinalisePatch): Promise<void> {
  await db
    .update(ingestRuns)
    .set({
      status: patch.status,
      rowsAttempted: patch.rowsAttempted ?? null,
      rowsSucceeded: patch.rowsSucceeded ?? null,
      rowsFailed: patch.rowsFailed ?? null,
      llmCalls: patch.llmCalls ?? null,
      llmCostUsd:
        typeof patch.llmCostUsd === "number" ? String(patch.llmCostUsd) : null,
      error: patch.error ?? null,
      reason: patch.reason ?? null,
      finishedAt: drizzleSql`now()`,
    })
    .where(eq(ingestRuns.runId, runId));
}

export async function getRun(runId: string): Promise<IngestRun | null> {
  const rows = await db.select().from(ingestRuns).where(eq(ingestRuns.runId, runId));
  return rows[0] ?? null;
}
