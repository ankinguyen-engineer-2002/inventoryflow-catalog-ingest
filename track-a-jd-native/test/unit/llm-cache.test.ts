/**
 * Cache decorator unit test.
 *
 * Verifies:
 *   • 1st call → cache miss → upstream invoked → result cached
 *   • 2nd identical call → cache hit → upstream NOT invoked
 *   • Different input → cache miss again
 */
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { CachedLLMProvider } from "../../src/ai/providers/cached.provider.js";
import type {
  EnrichmentRequest,
  EnrichmentResponse,
  ILLMProvider,
} from "../../src/ai/provider.js";

class CountingProvider implements ILLMProvider {
  readonly name = "counting";
  calls = 0;
  async enrich(req: EnrichmentRequest): Promise<EnrichmentResponse> {
    this.calls++;
    return {
      id: req.id,
      field: req.field,
      result: `call#${this.calls}: ${JSON.stringify(req.inputs)}`,
      meta: {
        provider: this.name,
        promptTemplateVer: "test-v1",
        cacheHit: false,
      },
    };
  }
}

let tmpDir: string;
let cachePath: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ifc-cache-"));
  cachePath = join(tmpDir, "cache.sqlite");
});

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

describe("CachedLLMProvider", () => {
  it("invokes upstream on first call, caches result", async () => {
    const upstream = new CountingProvider();
    const cached = new CachedLLMProvider(upstream, cachePath);

    const req: EnrichmentRequest = {
      id: "t1",
      field: "translate_cn_to_en",
      inputs: { cn: "把套" },
    };

    const r1 = await cached.enrich(req);
    expect(r1.meta.cacheHit).toBe(false);
    expect(upstream.calls).toBe(1);
    cached.close();
  });

  it("returns cached value on second identical call", async () => {
    const upstream = new CountingProvider();
    const cached = new CachedLLMProvider(upstream, cachePath);

    const req: EnrichmentRequest = {
      id: "t2",
      field: "translate_cn_to_en",
      inputs: { cn: "组合开关" },
    };

    await cached.enrich(req);
    const r2 = await cached.enrich(req);

    expect(r2.meta.cacheHit).toBe(true);
    expect(upstream.calls).toBe(1); // only the first call hit upstream
    cached.close();
  });

  it("treats different inputs as separate cache entries", async () => {
    const upstream = new CountingProvider();
    const cached = new CachedLLMProvider(upstream, cachePath);

    await cached.enrich({ id: "a", field: "translate_cn_to_en", inputs: { cn: "X" } });
    await cached.enrich({ id: "b", field: "translate_cn_to_en", inputs: { cn: "Y" } });

    expect(upstream.calls).toBe(2);
    cached.close();
  });
});
