/**
 * Ollama local LLM provider (production-autonomous path).
 *
 * Calls a locally-running Ollama instance via its HTTP API. Zero external
 * dependency, $0 cost, fully automated — the natural fit for production
 * runtimes that don't want vendor lock-in or per-call API spend.
 *
 * Model recommendations (2026):
 *   • `qwen2.5:7b`     — strong CN→EN translation (Alibaba foundation)
 *   • `qwen2-vl:7b`    — vision-capable for callout extraction
 *   • `llama3.2:3b`    — lighter for `infer_make`
 *
 * Setup:
 *   brew install ollama
 *   ollama serve &
 *   ollama pull qwen2.5:7b
 *
 * Switch the pipeline:
 *   LLM_PROVIDER=ollama OLLAMA_MODEL=qwen2.5:7b pnpm enrich
 */
import type {
  EnrichmentRequest,
  EnrichmentResponse,
  ILLMProvider,
} from "../provider.js";
import { logger } from "../../lib/logger.js";

const log = logger.child({ provider: "ollama" });

const PROMPT_TEMPLATE_VER = "ollama-translate-v1";

const TRANSLATE_PROMPT = (cn: string): string => `You are a translator for a powersports motorcycle/ATV parts catalog.

Translate this Chinese part name to natural English. Return ONLY the English translation, no explanation, no quotes, no extra punctuation.

Chinese: ${cn}

English:`;

export interface OllamaOptions {
  baseUrl: string;
  model: string;
  /** Timeout per request (ms). Local models are slow under load. */
  timeoutMs?: number;
}

export class OllamaProvider implements ILLMProvider {
  readonly name = "ollama";

  constructor(private readonly opts: OllamaOptions) {}

  async enrich(req: EnrichmentRequest): Promise<EnrichmentResponse> {
    if (req.field !== "translate_cn_to_en") {
      // Vision/callout extraction would use qwen2-vl + multimodal payload
      // — out of scope for this PoC; documented in ADR-007.
      return notImplementedResponse(req, this.name);
    }
    const cn = String(req.inputs.cn ?? "").trim();
    if (!cn) {
      return emptyInputResponse(req, this.name);
    }

    const start = Date.now();
    const body = {
      model: this.opts.model,
      prompt: TRANSLATE_PROMPT(cn),
      stream: false,
      options: {
        // Deterministic decoding for cache reuse.
        temperature: 0,
        top_p: 1,
        num_ctx: 512,
        num_predict: 64,
      },
    };

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.opts.timeoutMs ?? 30_000);

    try {
      const res = await fetch(`${this.opts.baseUrl}/api/generate`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!res.ok) {
        const text = await res.text();
        log.error({ status: res.status, text }, "ollama upstream error");
        return errorResponse(req, this.name);
      }

      const data = (await res.json()) as {
        response?: string;
        eval_count?: number;
        prompt_eval_count?: number;
      };

      const translated = cleanTranslation(data.response ?? "");
      const latency = Date.now() - start;

      // Confidence heuristic: short non-empty + ASCII-printable → "medium"
      // (local 7B models are good but not as crisp as Sonnet — never claim high)
      const confidence = scoreConfidence(translated);

      return {
        id: req.id,
        field: req.field,
        result: translated,
        confidence,
        meta: {
          provider: this.name,
          promptTemplateVer: PROMPT_TEMPLATE_VER,
          ...(data.prompt_eval_count !== undefined ? { tokensIn: data.prompt_eval_count } : {}),
          ...(data.eval_count !== undefined ? { tokensOut: data.eval_count } : {}),
          costUsd: 0,
          latencyMs: latency,
          cacheHit: false,
        },
      };
    } catch (err) {
      log.error({ err: (err as Error).message, model: this.opts.model }, "ollama call failed");
      return errorResponse(req, this.name);
    } finally {
      clearTimeout(timeout);
    }
  }
}

/**
 * Strip common LLM output artefacts: leading quotes, "Translation:" prefixes,
 * trailing newlines, trailing punctuation.
 */
function cleanTranslation(raw: string): string {
  let s = raw.trim();
  // Remove "English:" / "Translation:" prefixes if model leaked them
  s = s.replace(/^(English|Translation)\s*:\s*/i, "");
  // Strip surrounding quotes
  s = s.replace(/^["'`]+/, "").replace(/["'`]+$/, "");
  // Strip trailing periods/commas
  s = s.replace(/[.,;]+$/, "");
  // Collapse internal whitespace
  s = s.replace(/\s{2,}/g, " ").trim();
  return s;
}

function scoreConfidence(translated: string): "high" | "medium" | "low" {
  if (!translated) return "low";
  // Pure ASCII + 2-6 words = looks like a plausible part name
  if (/^[\x20-\x7E]+$/.test(translated)) {
    const words = translated.split(/\s+/).length;
    if (words >= 2 && words <= 6) return "medium";
    if (words === 1) return "medium";
    if (words > 6) return "low"; // overly verbose for a part name
  }
  return "low";
}

function notImplementedResponse(req: EnrichmentRequest, name: string): EnrichmentResponse {
  return {
    id: req.id,
    field: req.field,
    result: null,
    confidence: "low",
    meta: { provider: name, promptTemplateVer: PROMPT_TEMPLATE_VER, cacheHit: false },
  };
}

function emptyInputResponse(req: EnrichmentRequest, name: string): EnrichmentResponse {
  return {
    id: req.id,
    field: req.field,
    result: null,
    confidence: "low",
    meta: { provider: name, promptTemplateVer: PROMPT_TEMPLATE_VER, cacheHit: false },
  };
}

function errorResponse(req: EnrichmentRequest, name: string): EnrichmentResponse {
  return {
    id: req.id,
    field: req.field,
    result: null,
    confidence: "low",
    meta: {
      provider: name,
      promptTemplateVer: PROMPT_TEMPLATE_VER,
      cacheHit: false,
      costUsd: 0,
    },
  };
}
