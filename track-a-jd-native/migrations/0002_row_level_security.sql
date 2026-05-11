-- ============================================================
-- Migration 0002: enable Row-Level Security on dealer-owned tables.
--
-- Approach (per ADR-011):
--   • Every dealer-scoped table has a policy referencing the per-session
--     setting `app.current_dealer_id`.
--   • Application code MUST issue `SET LOCAL app.current_dealer_id = '<uuid>'`
--     at the start of every transaction handling tenant-scoped data.
--   • A "bypass" role exists for administrative jobs (ingest, audit).
--   • Roles other than `postgres` cannot disable RLS at the session level.
--
-- This migration is reversible by `ALTER TABLE ... DISABLE ROW LEVEL SECURITY`
-- on each table. Disabling is not the default; activate per-environment.
-- ============================================================

-- Bypass role for ingest workers and admin tasks.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rls_bypass') THEN
    CREATE ROLE rls_bypass NOLOGIN BYPASSRLS;
  END IF;
END
$$;

-- Application role; receives no superuser, no bypass.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_tenant') THEN
    CREATE ROLE app_tenant NOLOGIN;
  END IF;
END
$$;

-- Default placeholder so unset session var doesn't crash queries on
-- non-tenant-scoped paths. Tenant code MUST overwrite with SET LOCAL.
ALTER DATABASE catalog SET app.current_dealer_id = '00000000-0000-0000-0000-000000000000';

-- ============================================================
-- products
-- ============================================================
ALTER TABLE products ENABLE ROW LEVEL SECURITY;
ALTER TABLE products FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation_products ON products;
CREATE POLICY tenant_isolation_products ON products
  AS PERMISSIVE
  FOR ALL
  TO app_tenant
  USING (
    source_dealer_id IS NULL
    OR source_dealer_id = current_setting('app.current_dealer_id', true)::uuid
  )
  WITH CHECK (
    source_dealer_id IS NULL
    OR source_dealer_id = current_setting('app.current_dealer_id', true)::uuid
  );

-- ============================================================
-- product_images (inherits dealer scoping via products FK)
-- ============================================================
ALTER TABLE product_images ENABLE ROW LEVEL SECURITY;
ALTER TABLE product_images FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation_product_images ON product_images;
CREATE POLICY tenant_isolation_product_images ON product_images
  AS PERMISSIVE
  FOR ALL
  TO app_tenant
  USING (
    EXISTS (
      SELECT 1 FROM products p
      WHERE p.id = product_images.product_id
        AND (p.source_dealer_id IS NULL
             OR p.source_dealer_id = current_setting('app.current_dealer_id', true)::uuid)
    )
  );

-- ============================================================
-- stream_events
-- ============================================================
ALTER TABLE stream_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE stream_events FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation_stream_events ON stream_events;
CREATE POLICY tenant_isolation_stream_events ON stream_events
  AS PERMISSIVE
  FOR ALL
  TO app_tenant
  USING (dealer_id = current_setting('app.current_dealer_id', true)::uuid)
  WITH CHECK (dealer_id = current_setting('app.current_dealer_id', true)::uuid);

-- ============================================================
-- ingest_runs
-- ============================================================
ALTER TABLE ingest_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingest_runs FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation_ingest_runs ON ingest_runs;
CREATE POLICY tenant_isolation_ingest_runs ON ingest_runs
  AS PERMISSIVE
  FOR ALL
  TO app_tenant
  USING (
    dealer_id IS NULL
    OR dealer_id = current_setting('app.current_dealer_id', true)::uuid
  );

-- ============================================================
-- dealer_pattern_bindings — tenant can read its own; admin manages.
-- ============================================================
ALTER TABLE dealer_pattern_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE dealer_pattern_bindings FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_read_bindings ON dealer_pattern_bindings;
CREATE POLICY tenant_read_bindings ON dealer_pattern_bindings
  AS PERMISSIVE
  FOR SELECT
  TO app_tenant
  USING (dealer_id = current_setting('app.current_dealer_id', true)::uuid);

-- Grant base read privileges to app_tenant; policy filters rows.
GRANT SELECT, INSERT, UPDATE, DELETE ON
  products, product_images, stream_events, ingest_runs, dealer_pattern_bindings
TO app_tenant;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_tenant;

-- NB: ingest pipelines connect via the rls_bypass role (or postgres role).
-- See docs/TRACK_A.md §11.3 for application-code conventions.
