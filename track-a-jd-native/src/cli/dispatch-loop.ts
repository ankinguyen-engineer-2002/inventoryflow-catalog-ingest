#!/usr/bin/env node
/**
 * Metadata-driven dispatch control loop (ADR-014, minimal viable).
 *
 * Scans `dealer_pattern_bindings`, evaluates `should_run` per binding, and
 * dispatches due bindings to their handler module via the existing BullMQ
 * queues. This is the runtime that consumes the registry seeded by
 * `pnpm seed:mdcp`.
 *
 * Evaluation rules (cron-smart skip):
 *   1. binding.schedule = 'event-driven'         → skip (no schedule check)
 *   2. binding.schedule = 'on-source-change'     → check SHA-256; skip if unchanged
 *   3. binding.last_run_at + freshness_sla > now → skip (not yet due)
 *   4. otherwise                                 → enqueue handler
 *
 * Runs once and exits. In production this is invoked periodically by cron
 * or by a Dagster sensor. Wrapping it in a loop is straightforward; kept
 * single-shot here for predictable testing.
 *
 * Usage:  pnpm dispatch
 */
import { parseArgs } from "node:util";
import { createHash } from "node:crypto";
import { statSync, existsSync } from "node:fs";
import { sql, closeDb } from "../storage/db/client.js";
import { parseFileQueue } from "../queue/queues.js";
import { logger } from "../lib/logger.js";

interface Binding {
  binding_id: number;
  dealer_id: string;
  pattern_name: string;
  pattern_type: string;
  handler_module: string;
  schedule: string | null;
  freshness_sla: string | null;
  params: Record<string, unknown>;
  last_run_sha256: string | null;
  last_run_at: string | null;
}

interface DispatchDecision {
  binding_id: number;
  dealer_id: string;
  pattern_name: string;
  decision: "DISPATCHED" | "SKIPPED";
  reason: string;
}

interface CliOptions {
  dryRun: boolean;
  bindingId: number | null;
}

function parseCliArgs(): CliOptions {
  const { values } = parseArgs({
    options: {
      "dry-run": { type: "boolean", default: false },
      "binding-id": { type: "string" },
    },
  });
  return {
    dryRun: Boolean(values["dry-run"]),
    ...(values["binding-id"] ? { bindingId: Number(values["binding-id"]) } : { bindingId: null }),
  };
}

async function main(): Promise<void> {
  const opts = parseCliArgs();
  const log = logger.child({ cli: "dispatch-loop", dryRun: opts.dryRun });
  log.info("Dispatch loop starting");

  const bindings = await loadActiveBindings(opts.bindingId);
  log.info({ count: bindings.length }, "Bindings loaded");

  const decisions: DispatchDecision[] = [];

  for (const b of bindings) {
    const decision = await evaluateBinding(b);
    decisions.push(decision);

    if (decision.decision === "DISPATCHED" && !opts.dryRun) {
      await dispatchBinding(b);
    }
  }

  // Audit log: persist decisions to ingest_runs for observability,
  // marking SKIPPED dispatches so operators can see why a binding didn't fire.
  for (const d of decisions) {
    if (d.decision === "SKIPPED") {
      await sql`
        INSERT INTO ingest_runs (dealer_id, source_file, source_sha256, status, reason)
        VALUES (${d.dealer_id}::uuid, ${"binding:" + d.pattern_name}, 'n/a', 'SKIPPED', ${d.reason})
      `;
    }
  }

  log.info(
    {
      total: decisions.length,
      dispatched: decisions.filter((d) => d.decision === "DISPATCHED").length,
      skipped: decisions.filter((d) => d.decision === "SKIPPED").length,
    },
    "Dispatch loop complete",
  );

  for (const d of decisions) {
    log.info(d, "decision");
  }
}

async function loadActiveBindings(bindingId: number | null): Promise<Binding[]> {
  const rows = bindingId
    ? await sql<Binding[]>`
        SELECT
          b.id AS binding_id, b.dealer_id, b.pattern_name,
          p.pattern_type, p.handler_module,
          coalesce(b.schedule, p.default_schedule) AS schedule,
          coalesce(b.freshness_sla, p.default_freshness_sla) AS freshness_sla,
          b.params, b.last_run_sha256, b.last_run_at::text
        FROM dealer_pattern_bindings b
        JOIN ingestion_patterns p ON p.pattern_name = b.pattern_name
        WHERE b.id = ${bindingId} AND b.enabled = true
      `
    : await sql<Binding[]>`
        SELECT
          b.id AS binding_id, b.dealer_id, b.pattern_name,
          p.pattern_type, p.handler_module,
          coalesce(b.schedule, p.default_schedule) AS schedule,
          coalesce(b.freshness_sla, p.default_freshness_sla) AS freshness_sla,
          b.params, b.last_run_sha256, b.last_run_at::text
        FROM dealer_pattern_bindings b
        JOIN ingestion_patterns p ON p.pattern_name = b.pattern_name
        WHERE b.enabled = true
        ORDER BY b.id
      `;
  return rows.map((r) => ({ ...r, params: r.params ?? {} }));
}

async function evaluateBinding(b: Binding): Promise<DispatchDecision> {
  const base = {
    binding_id: b.binding_id,
    dealer_id: b.dealer_id,
    pattern_name: b.pattern_name,
  };

  // Event-driven bindings are triggered externally, not by this loop.
  if (b.schedule === "event-driven") {
    return { ...base, decision: "SKIPPED", reason: "EVENT_DRIVEN" };
  }

  // FILE_BATCH with on-source-change: hash the file, skip if unchanged.
  if (b.schedule === "on-source-change") {
    const sourceGlob = b.params["source_glob"] as string | undefined;
    if (!sourceGlob) {
      return { ...base, decision: "SKIPPED", reason: "NO_SOURCE_GLOB" };
    }
    const sourcePath = sourceGlob.replace("s3://", "../shared/sample-data/")
                                  .replace(/\/\*\.xlsx$/, "/example.xlsx");
    if (!existsSync(sourcePath)) {
      return { ...base, decision: "SKIPPED", reason: "SOURCE_MISSING" };
    }
    const currentSha = sha256OfFile(sourcePath);
    if (b.last_run_sha256 === currentSha) {
      return { ...base, decision: "SKIPPED", reason: "UNCHANGED_SOURCE" };
    }
    return { ...base, decision: "DISPATCHED", reason: "SOURCE_CHANGED" };
  }

  // Freshness SLA check: run if last_run_at + sla has elapsed.
  if (b.last_run_at && b.freshness_sla) {
    const slaMs = parseIso8601Duration(b.freshness_sla);
    const lastRun = new Date(b.last_run_at).getTime();
    if (Date.now() < lastRun + slaMs) {
      return { ...base, decision: "SKIPPED", reason: "FRESH_WITHIN_SLA" };
    }
  }

  return { ...base, decision: "DISPATCHED", reason: "DUE_OR_FIRST_RUN" };
}

async function dispatchBinding(b: Binding): Promise<void> {
  // Only FILE_BATCH is wired in the minimal dispatcher. Other pattern types
  // (API_PUSH, CDC) are event-driven and skipped above.
  if (b.pattern_type !== "FILE_BATCH") return;

  const sourceGlob = b.params["source_glob"] as string | undefined;
  const sourcePath = sourceGlob
    ? sourceGlob.replace("s3://", "../shared/sample-data/").replace(/\/\*\.xlsx$/, "/example.xlsx")
    : "../shared/sample-data/example.xlsx";
  if (!existsSync(sourcePath)) return;

  const currentSha = sha256OfFile(sourcePath);

  // Enqueue parse-file job; the rest of the pipeline takes over.
  await parseFileQueue.add(
    "parse-file",
    {
      runId: crypto.randomUUID(),
      dealerId: b.dealer_id,
      filePath: sourcePath,
      fileSha256: currentSha,
    },
    { jobId: `dispatch:${b.binding_id}:${currentSha}` },
  );

  // Update binding so subsequent loop runs see the new hash.
  await sql`
    UPDATE dealer_pattern_bindings
    SET last_run_sha256 = ${currentSha},
        last_run_at = now()
    WHERE id = ${b.binding_id}
  `;
}

function sha256OfFile(path: string): string {
  const buf = require("node:fs").readFileSync(path);
  return createHash("sha256").update(buf).digest("hex");
}

/** Parse ISO 8601 duration like PT24H, PT5M, PT1H30M into milliseconds. */
function parseIso8601Duration(d: string): number {
  const match = d.match(/^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$/);
  if (!match) return 0;
  const hours = Number(match[1] ?? 0);
  const mins = Number(match[2] ?? 0);
  const secs = Number(match[3] ?? 0);
  return ((hours * 60 + mins) * 60 + secs) * 1000;
}

main()
  .then(async () => {
    await closeDb();
    process.exit(0);
  })
  .catch(async (err: unknown) => {
    logger.error({ err }, "dispatch-loop failed");
    await closeDb().catch(() => {});
    process.exit(1);
  });

// stat type used only to satisfy module surface
void statSync;
