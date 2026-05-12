-- Vision-extracted callouts per unique image.
--
-- One row per image SHA-256 (deduped at the image level — same image used
-- across multiple products shares one callout extraction).
--
-- Populated by `pnpm extract-callouts` which reads the shared LLM cache
-- (../shared/llm-cache.jsonl) populated by either:
--   • Groq Vision (free tier, Llama-4 Scout 17B) — current production demo
--   • Local Ollama qwen2.5vl:7b — air-gapped fallback for dealer-data-sensitive deploys
--
-- See docs/decisions/ADR-007-llm-provider-cost-strategy.md §Vision.

CREATE TABLE IF NOT EXISTS image_callouts (
  image_sha256        text PRIMARY KEY,
  callouts            jsonb NOT NULL DEFAULT '[]'::jsonb,
  callout_count       integer NOT NULL DEFAULT 0,
  confidence          text NOT NULL DEFAULT 'low',
  vision_provider     text NOT NULL,
  cache_hit           boolean NOT NULL DEFAULT true,
  source_sheets       jsonb NOT NULL DEFAULT '[]'::jsonb,
  image_size_bytes    integer,
  extracted_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_image_callouts_count
  ON image_callouts (callout_count);

CREATE INDEX IF NOT EXISTS ix_image_callouts_provider
  ON image_callouts (vision_provider);

COMMENT ON TABLE image_callouts IS
  'Vision LLM-extracted callout numbers per unique schematic image, deduped by SHA-256. Populated by pnpm extract-callouts reading the shared LLM cache.';
