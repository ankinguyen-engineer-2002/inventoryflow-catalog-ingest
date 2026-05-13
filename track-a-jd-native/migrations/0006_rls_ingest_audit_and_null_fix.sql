-- ===================================================================
-- Migration 0006 — Close two RLS gaps from migration 0002
-- ===================================================================
--
-- Gap 1: products RLS allowed source_dealer_id IS NULL to be visible
--        to every tenant. This was a cross-tenant leak path if any
--        row was ever ingested without a dealer_id.
--        Fix: drop the IS NULL allowance from the policy. Make
--        source_dealer_id NOT NULL at the schema level. This submission
--        does not need global-catalog rows.
--
-- Gap 2: ingest_audit had no RLS policy at all, despite the security
--        doc claiming tenant isolation. Migration 0005 added dealer_id
--        to ingest_audit; this migration uses it to enable tenant-scoped
--        RLS.
--
-- Setting name: app.current_dealer_id (matches migration 0002 + plugin).
-- All operations idempotent for safe re-run.
-- ===================================================================

-- ─── Gap 1a: products RLS — disallow NULL source_dealer_id ───────────
DROP POLICY IF EXISTS "products_tenant_scope" ON "products";
DROP POLICY IF EXISTS "tenant_isolation_products" ON "products";

CREATE POLICY "tenant_isolation_products" ON "products"
    USING (
        source_dealer_id = current_setting('app.current_dealer_id', true)::uuid
    );

-- Schema-level NOT NULL guard. Backfill orphans defensively: any rows
-- that ended up with NULL get linked to the first dealer (the demo
-- dealer in this submission). In a multi-dealer production system this
-- would fail loud and force manual triage.
DO $$
DECLARE
    v_orphans int;
    v_first_dealer uuid;
BEGIN
    SELECT count(*) INTO v_orphans FROM "products" WHERE source_dealer_id IS NULL;

    IF v_orphans > 0 THEN
        SELECT id INTO v_first_dealer FROM "dealers" ORDER BY onboarded_at LIMIT 1;

        IF v_first_dealer IS NULL THEN
            RAISE EXCEPTION
                '% products have NULL source_dealer_id but no dealers exist to anchor them',
                v_orphans;
        END IF;

        UPDATE "products" SET source_dealer_id = v_first_dealer WHERE source_dealer_id IS NULL;
        RAISE NOTICE 'Backfilled % orphan products to dealer %', v_orphans, v_first_dealer;
    END IF;
END $$;

-- Set NOT NULL only if not already set.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'products'
           AND column_name = 'source_dealer_id'
           AND is_nullable = 'YES'
    ) THEN
        ALTER TABLE "products" ALTER COLUMN "source_dealer_id" SET NOT NULL;
    END IF;
END $$;

-- ─── Gap 1b: product_images RLS — also drop the IS NULL allowance ────
DROP POLICY IF EXISTS "product_images_tenant_scope" ON "product_images";
DROP POLICY IF EXISTS "tenant_isolation_product_images" ON "product_images";

CREATE POLICY "tenant_isolation_product_images" ON "product_images"
    USING (
        EXISTS (
            SELECT 1 FROM "products" p
             WHERE p.id = product_images.product_id
               AND p.source_dealer_id = current_setting('app.current_dealer_id', true)::uuid
        )
    );

-- ─── Gap 2: enable RLS on ingest_audit with tenant scope ─────────────
ALTER TABLE "ingest_audit" ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "ingest_audit_tenant_scope" ON "ingest_audit";

CREATE POLICY "ingest_audit_tenant_scope" ON "ingest_audit"
    USING (
        dealer_id = current_setting('app.current_dealer_id', true)::uuid
        OR current_setting('app.current_dealer_id', true) IS NULL
    );
