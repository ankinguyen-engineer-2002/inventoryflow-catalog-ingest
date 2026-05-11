/**
 * Mock LLM provider — deterministic fixtures.
 *
 * Used in:
 *   • Unit tests
 *   • As the safety-net behaviour when LLM_PROVIDER=mock
 *
 * The mock is intentionally dumb: it returns canned strings, not LLM
 * output. Real correctness comes from the cached + handoff providers.
 */
import type { EnrichmentRequest, EnrichmentResponse, ILLMProvider } from "../provider.js";

/** Hard-coded translations for a few common parts seen in the test data. */
const KNOWN_TRANSLATIONS: Record<string, string> = {
  "把套": "handlebar grip",
  "组合开关": "multi-function switch",
  "钢制方向把": "steel handlebar",
  "护套芯": "padding insert",
  "熄火开关": "stop switch",
  "油门线": "throttle cable",
  "加速器": "accelerator",
  "塑料扎带": "plastic cable tie",
  "风门线": "choke cable",
};

export class MockProvider implements ILLMProvider {
  readonly name = "mock";

  async enrich(req: EnrichmentRequest): Promise<EnrichmentResponse> {
    let result: string | number[] | null = null;
    if (req.field === "translate_cn_to_en") {
      const cn = req.inputs.cn ?? "";
      result = KNOWN_TRANSLATIONS[cn] ?? `[mock-translate: ${cn.slice(0, 20)}]`;
    } else if (req.field === "infer_make") {
      result = "Kayo";
    } else if (req.field === "extract_callouts") {
      result = []; // mock can't see images
    }

    return {
      id: req.id,
      field: req.field,
      result,
      confidence: "low",
      meta: {
        provider: this.name,
        promptTemplateVer: "mock-v1",
        cacheHit: false,
      },
    };
  }
}
