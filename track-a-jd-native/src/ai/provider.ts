/**
 * LLM provider abstraction.
 *
 * Every LLM call goes through this interface. Implementations:
 *   • MockProvider — fixture-backed, used in tests + as the universal
 *                    fallback so the reviewer never sees a missing-cache
 *                    crash.
 *   • CachedProvider — decorator wrapping any upstream; SQLite-backed.
 *                      Default for reviewer runs (LLM_PROVIDER=cached).
 *   • ClaudeCodeHandoffProvider — emits task files for the operator
 *                                 (me) to translate via the Claude Max
 *                                 session, then reads results back.
 *                                 Used to seed the cache.
 *   • OllamaProvider, GeminiFreeProvider, AnthropicBatchProvider —
 *                      production-target implementations (stubs in this
 *                      submission). See ADR-007.
 */

/** A field needing enrichment. */
export type EnrichmentField = "translate_cn_to_en" | "extract_callouts" | "infer_make";

export interface EnrichmentRequest {
  /** Stable opaque task id used by the handoff provider. */
  id: string;
  field: EnrichmentField;
  inputs: {
    /** For translate_cn_to_en: the Chinese text. */
    cn?: string;
    /** For extract_callouts: R2 key of the schematic image. */
    image_r2_key?: string;
    /** For infer_make: model code. */
    model_code?: string;
  };
  /** Optional context the model may use for better answers. */
  context?: Record<string, unknown>;
}

export interface EnrichmentResponse {
  id: string;
  field: EnrichmentField;
  /** Result is the natural output for the field — string for translate/infer, number[] for callouts. */
  result: string | number[] | null;
  /** Confidence, if the provider can supply it. */
  confidence?: "high" | "medium" | "low";
  /** Provider metadata for audit. */
  meta: {
    provider: string;
    promptTemplateVer: string;
    tokensIn?: number;
    tokensOut?: number;
    costUsd?: number;
    latencyMs?: number;
    cacheHit?: boolean;
  };
}

export interface ILLMProvider {
  readonly name: string;
  enrich(req: EnrichmentRequest): Promise<EnrichmentResponse>;
}
