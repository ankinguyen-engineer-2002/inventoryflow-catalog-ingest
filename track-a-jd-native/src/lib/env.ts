/**
 * Runtime environment validation.
 *
 * All process.env reads should go through this module. If a required
 * variable is missing or malformed, fail fast on import — better than
 * a NullPointerException three layers deep at runtime.
 *
 * Why Zod? compile-time + runtime guarantee with one schema.
 */
import { z } from "zod";
import { config as loadDotenv } from "dotenv";

// Load .env once at module init. Subsequent reads use process.env.
loadDotenv();

const Schema = z.object({
  // ── Application ─────────────────────────────────────────────
  NODE_ENV: z.enum(["development", "test", "production"]).default("development"),
  LOG_LEVEL: z.enum(["trace", "debug", "info", "warn", "error", "fatal"]).default("info"),
  APP_PORT: z.coerce.number().int().positive().default(3000),

  // ── Database ────────────────────────────────────────────────
  DATABASE_URL: z.string().url().default("postgres://dev:dev@localhost:5432/catalog"),
  DB_POOL_SIZE: z.coerce.number().int().positive().default(10),

  // ── Redis / queues ──────────────────────────────────────────
  REDIS_URL: z.string().default("redis://localhost:6379"),

  // ── Object storage (S3-compatible) ──────────────────────────
  S3_ENDPOINT: z.string().url().default("http://localhost:9000"),
  S3_REGION: z.string().default("auto"),
  S3_BUCKET: z.string().default("catalog"),
  S3_ACCESS_KEY: z.string().default("minioadmin"),
  S3_SECRET_KEY: z.string().default("minioadmin"),
  S3_FORCE_PATH_STYLE: z.coerce.boolean().default(true),

  // ── LLM provider ────────────────────────────────────────────
  LLM_PROVIDER: z
    .enum([
      "mock",
      "cached",
      "claude-code-handoff",
      "ollama",
      "ollama-vision",
      "gemini",
      "anthropic",
    ])
    .default("cached"),
  LLM_CACHE_PATH: z.string().default("../shared/llm-cache.jsonl"),

  // Provider-specific (optional)
  OLLAMA_URL: z.string().url().default("http://localhost:11434"),
  OLLAMA_MODEL: z.string().default("qwen2.5:7b"),
  OLLAMA_VISION_MODEL: z.string().default("qwen2.5vl:7b"),
  GEMINI_API_KEY: z.string().optional(),
  ANTHROPIC_API_KEY: z.string().optional(),

  // ── Streaming (opt-in) ──────────────────────────────────────
  STREAMING_ENABLED: z.coerce.boolean().default(false),
  REDPANDA_BROKER: z.string().default("localhost:9092"),

  // ── Observability ───────────────────────────────────────────
  OTEL_SERVICE_NAME: z.string().default("inventoryflow-catalog-ingest"),
  OTEL_EXPORTER_OTLP_ENDPOINT: z.string().url().optional(),
});

export type Env = z.infer<typeof Schema>;

function parseEnv(): Env {
  const parsed = Schema.safeParse(process.env);
  if (!parsed.success) {
    // Print all issues, then die. No partial-config startups.
    const errors = parsed.error.issues
      .map((i) => `  • ${i.path.join(".")}: ${i.message}`)
      .join("\n");
    // eslint-disable-next-line no-console
    console.error(`Invalid environment configuration:\n${errors}`);
    process.exit(1);
  }
  return parsed.data;
}

export const env: Env = parseEnv();
