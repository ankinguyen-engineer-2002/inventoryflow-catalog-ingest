/**
 * Batch ingest runs.
 *
 *   POST /runs          → enqueue a file for ingestion (202 Accepted + run_id)
 *   GET  /runs/:runId   → check run status
 */
import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { createRun, getRun } from "../../storage/db/repositories/runs.repo.js";
import { parseFileQueue } from "../../queue/queues.js";

const PostRunsBody = z.object({
  filePath: z.string().min(1),
  fileSha256: z.string().min(8),
});

export async function runsRoutes(app: FastifyInstance): Promise<void> {
  app.post("/runs", async (req, reply) => {
    const parsed = PostRunsBody.safeParse(req.body);
    if (!parsed.success) {
      return reply.code(400).send({ error: "invalid_body", issues: parsed.error.issues });
    }

    const run = await createRun({
      dealerId: req.dealerId,
      sourceFile: parsed.data.filePath,
      sourceSha256: parsed.data.fileSha256,
      status: "QUEUED",
    });

    await parseFileQueue.add(
      "parse-file",
      {
        runId: run.runId,
        dealerId: run.dealerId,
        filePath: parsed.data.filePath,
        fileSha256: parsed.data.fileSha256,
      },
      { jobId: run.runId },
    );

    return reply.code(202).send({ runId: run.runId, status: run.status });
  });

  app.get<{ Params: { runId: string } }>("/runs/:runId", async (req, reply) => {
    const run = await getRun(req.params.runId);
    if (!run) return reply.code(404).send({ error: "run_not_found" });
    return run;
  });
}
