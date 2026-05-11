-- Reissue the products unique index with NULLS NOT DISTINCT.
--
-- Why: PostgreSQL's default behaviour treats NULLs as distinct in UNIQUE
-- constraints. That breaks idempotent re-ingestion for rows where
-- source_dealer_id is NULL (eg before MDCP dealer-binding is wired):
-- every re-run inserts a duplicate row instead of conflicting + upserting.
--
-- NULLS NOT DISTINCT (PG 15+) treats NULLs as equal for uniqueness,
-- which restores the upsert path.

DROP INDEX IF EXISTS ux_products_partnum_dealer;

CREATE UNIQUE INDEX ux_products_partnum_dealer
  ON products (part_number_norm, source_dealer_id)
  NULLS NOT DISTINCT;
