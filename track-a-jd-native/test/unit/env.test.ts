/**
 * Smoke test for env loader.
 * We import the module and verify it doesn't throw with default values.
 *
 * Note: env.ts calls process.exit(1) on parse failure, so we don't try
 * to test that path in-process — that would kill the test runner.
 */
import { describe, expect, it } from "vitest";

describe("env loader", () => {
  it("loads with defaults when no env vars are set explicitly", async () => {
    // Force a fresh import; env.ts loads once at module init.
    const mod = await import("../../src/lib/env.js");
    expect(mod.env.NODE_ENV).toMatch(/^(development|test|production)$/);
    expect(mod.env.DATABASE_URL).toContain("postgres://");
    expect(mod.env.S3_BUCKET).toBeTruthy();
    expect(mod.env.LLM_PROVIDER).toBeTruthy();
  });
});
