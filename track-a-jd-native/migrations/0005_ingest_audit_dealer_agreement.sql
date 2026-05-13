-- ===================================================================
-- Migration 0005 — Add dealer_id + agreement columns to ingest_audit
-- ===================================================================
--
-- The SLO/observability doc (docs/12-slo-observability.md) claims
-- per-dealer LLM cost aggregation and an "agreement" field for
-- the LLM-vs-dealer translation comparison. The original ingest_audit
-- schema (migration 0000) was missing both.
--
-- Fix: add the columns (idempotent — IF NOT EXISTS). Backfill dealer_id
-- from the joined ingest_runs row. The agreement column starts NULL;
-- the enrich CLI populates it on audit pass.
--
-- The CHECK constraint enforces the three allowed values.
-- ===================================================================

ALTER TABLE "ingest_audit"
    ADD COLUMN IF NOT EXISTS "dealer_id" uuid;

ALTER TABLE "ingest_audit"
    ADD COLUMN IF NOT EXISTS "agreement" text;

-- Add the CHECK constraint separately so re-runs are safe.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'ingest_audit'::regclass
           AND conname = 'ingest_audit_agreement_check'
    ) THEN
        ALTER TABLE "ingest_audit"
            ADD CONSTRAINT "ingest_audit_agreement_check"
            CHECK (agreement IS NULL OR agreement IN ('agree', 'partial', 'disagree'));
    END IF;
END $$;

-- Backfill dealer_id from ingest_runs.dealer_id where the FK joins.
-- Idempotent: only updates rows where dealer_id is still NULL.
UPDATE "ingest_audit" a
   SET dealer_id = r.dealer_id
  FROM "ingest_runs" r
 WHERE a.run_id = r.run_id
   AND a.dealer_id IS NULL;

CREATE INDEX IF NOT EXISTS "ix_ingest_audit_dealer_created"
    ON "ingest_audit" (dealer_id, created_at DESC);

CREATE INDEX IF NOT EXISTS "ix_ingest_audit_agreement"
    ON "ingest_audit" (agreement)
    WHERE agreement IS NOT NULL;
