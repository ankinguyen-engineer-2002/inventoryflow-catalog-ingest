-- Postgres init script — runs once on first container boot.
-- Enables extensions our schema depends on.

CREATE EXTENSION IF NOT EXISTS pgcrypto;       -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pg_trgm;        -- trigram GIN for name search
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;  -- query stats

-- A schema for application-managed metadata kept separate from public.
CREATE SCHEMA IF NOT EXISTS app;
