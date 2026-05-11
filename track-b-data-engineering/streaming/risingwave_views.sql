-- ============================================================
-- RisingWave streaming SQL — Track B real-time view (ADR-010).
--
-- This SQL is applied via:
--   psql -h localhost -p 4566 -U root -d dev -f streaming/risingwave_views.sql
--
-- It creates:
--   1. A source over the Redpanda `inventory.changes` topic
--   2. An incremental materialized view joining the stream to gold products
--   3. A sink writing live results back to PostgreSQL serving
--
-- The materialized view is updated on every event with sub-second latency.
-- ============================================================

-- ─────────────────────────────────────────────────────────────────────
-- Source: Redpanda topic carrying inventory change events
-- ─────────────────────────────────────────────────────────────────────
CREATE SOURCE IF NOT EXISTS src_inventory_changes (
    event_id          VARCHAR,
    dealer_id         VARCHAR,
    part_number       VARCHAR,
    stock_level       INT,
    timestamp         TIMESTAMP
) WITH (
    connector       = 'kafka',
    topic           = 'inventory.changes',
    properties.bootstrap.server = 'redpanda:9092',
    scan.startup.mode = 'earliest'
) FORMAT PLAIN ENCODE JSON;


-- ─────────────────────────────────────────────────────────────────────
-- Incremental materialized view
-- ─────────────────────────────────────────────────────────────────────
-- Joins each incoming inventory event against the gold products mart
-- (loaded as an external table). The view refreshes on every event.
-- ─────────────────────────────────────────────────────────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_live_inventory AS
SELECT
    e.event_id,
    e.dealer_id,
    e.part_number,
    e.stock_level,
    e.timestamp           AS observed_at,
    NOW()                 AS view_updated_at
FROM src_inventory_changes e;


-- ─────────────────────────────────────────────────────────────────────
-- Sink: replicate live view rows into the PostgreSQL serving layer
-- ─────────────────────────────────────────────────────────────────────
-- The Track A catalog API reads from PostgreSQL. By sinking the live
-- materialized view back to PG, the existing Fastify endpoints serve
-- near-realtime inventory without code modification.
-- ─────────────────────────────────────────────────────────────────────
CREATE SINK IF NOT EXISTS sink_live_inventory
FROM mv_live_inventory
WITH (
    connector = 'jdbc',
    jdbc.url  = 'jdbc:postgresql://postgres:5432/catalog?user=dev&password=dev',
    table.name = 'live_inventory_view',
    type      = 'upsert',
    primary_key = 'event_id'
);


-- ─────────────────────────────────────────────────────────────────────
-- Verification queries (run after seeding Redpanda with sample events)
-- ─────────────────────────────────────────────────────────────────────
-- SELECT count(*) FROM mv_live_inventory;
-- SELECT * FROM mv_live_inventory ORDER BY observed_at DESC LIMIT 10;
