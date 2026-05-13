-- ===================================================================
-- Migration 0004 — Fix part_number_norm generated-column whitespace bug
-- ===================================================================
--
-- Bug: migration 0000 generated the column as
--     regexp_replace(part_number, 's', '', 'g')
-- This strips the LITERAL letter 's', not whitespace. The Drizzle schema
-- intended `\s` (whitespace regex class) but the escape was lost during
-- migration generation.
--
-- Consequence: part numbers containing 's' or 'S' collide with the
-- whitespace-stripped form (e.g. "FAS-123" normalises to "FA-123",
-- which collides with "FA 123"). This breaks the idempotent upsert
-- key from migration 0001.
--
-- Fix: drop the unique index, drop the broken column, recreate with
-- POSIX whitespace class `[[:space:]]+`, recreate the unique index.
--
-- Backward-compat: any data already ingested with the broken normaliser
-- needs to be re-normalised. Since `part_number_norm` is GENERATED
-- ALWAYS, dropping + adding the column with the correct expression
-- forces Postgres to recompute every row. No data loss.
-- ===================================================================

BEGIN;

-- 1. Drop the unique index that depends on the broken column.
DROP INDEX IF EXISTS "ux_products_partnum_dealer";

-- 2. Drop the broken generated column.
ALTER TABLE "products" DROP COLUMN "part_number_norm";

-- 3. Recreate the column with the correct POSIX whitespace regex.
--    [[:space:]]+ matches any run of whitespace characters and is
--    portable across Postgres versions (\s requires PCRE which Drizzle
--    sometimes loses in escape encoding).
ALTER TABLE "products"
    ADD COLUMN "part_number_norm" text
    GENERATED ALWAYS AS (
        upper(regexp_replace(part_number, '[[:space:]]+', '', 'g'))
    ) STORED;

-- 4. Recreate the unique index with NULLS NOT DISTINCT (per migration 0001).
CREATE UNIQUE INDEX "ux_products_partnum_dealer"
    ON "products" (part_number_norm, source_dealer_id) NULLS NOT DISTINCT;

-- 5. Smoke-test the new behaviour. These assertions must hold.
DO $$
DECLARE
    v_normalised text;
BEGIN
    -- "AB S-123" → "ABS-123" (whitespace stripped, S preserved)
    SELECT upper(regexp_replace('AB S-123', '[[:space:]]+', '', 'g')) INTO v_normalised;
    IF v_normalised <> 'ABS-123' THEN
        RAISE EXCEPTION 'part_number_norm smoke-test failed for "AB S-123": got %', v_normalised;
    END IF;

    -- "FAS-123" → "FAS-123" (S preserved, no whitespace)
    SELECT upper(regexp_replace('FAS-123', '[[:space:]]+', '', 'g')) INTO v_normalised;
    IF v_normalised <> 'FAS-123' THEN
        RAISE EXCEPTION 'part_number_norm smoke-test failed for "FAS-123": got %', v_normalised;
    END IF;

    -- "ab\tc\nd" (tab + newline) → "ABCD"
    SELECT upper(regexp_replace(E'ab\tc\nd', '[[:space:]]+', '', 'g')) INTO v_normalised;
    IF v_normalised <> 'ABCD' THEN
        RAISE EXCEPTION 'part_number_norm smoke-test failed for mixed whitespace: got %', v_normalised;
    END IF;

    RAISE NOTICE 'part_number_norm smoke-tests passed: ABS-123, FAS-123, ABCD';
END $$;

COMMIT;
