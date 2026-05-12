/**
 * Ollama Vision provider — qwen2.5vl:7b local inference.
 *
 * Implements `extract_callouts` of the ILLMProvider interface. Sends a
 * base64-encoded schematic image to local Ollama, parses the response
 * for callout numbers ("1", "2", "1-1", "1-6L", ...).
 *
 * Symmetric with Python implementation at
 * `track-b-data-engineering/dagster_project/ai/ollama_vision.py`. Both
 * share the same `shared/llm-cache.jsonl` file — once the Python script
 * `scripts/vision_extract_all.py` populates the cache, Track A reads it
 * here without any further inference.
 *
 * Setup:
 *   brew install --cask ollama
 *   ollama serve &
 *   ollama pull qwen2.5vl:7b
 *
 * Switch the pipeline:
 *   LLM_PROVIDER=ollama-vision pnpm enrich
 */
import type {
  EnrichmentRequest,
  EnrichmentResponse,
  ILLMProvider,
} from "../provider.js";
import { logger } from "../../lib/logger.js";

const log = logger.child({ provider: "ollama-vision" });

const PROMPT_TEMPLATE_VER = "ollama-vision-callouts-v1";

const CALLOUT_PROMPT = `You are inspecting an exploded-view parts diagram from a motorcycle/ATV \
service catalog. The diagram has small numeric labels (callouts) like 1, 2, 3, or sometimes \
sub-callouts like "1-1", "1-6L", "1-6R".

Look carefully at the image and list every callout number you can read. Return ONLY a JSON \
array of strings, no commentary. Example: ["1", "2", "3", "4", "5", "1-1", "1-6L"]

If you cannot identify any callout numbers, return [].`;

export interface OllamaVisionOptions {
  baseUrl?: string;
  model?: string;
  timeoutMs?: number;
}

export class OllamaVisionProvider implements ILLMProvider {
  readonly name = "ollama-vision";

  private readonly baseUrl: string;
  private readonly model: string;
  private readonly timeoutMs: number;

  constructor(opts: OllamaVisionOptions = {}) {
    this.baseUrl = opts.baseUrl ?? "http://localhost:11434";
    this.model = opts.model ?? "qwen2.5vl:7b";
    this.timeoutMs = opts.timeoutMs ?? 120_000;
  }

  async enrich(req: EnrichmentRequest): Promise<EnrichmentResponse> {
    if (req.field !== "extract_callouts") {
      return this.fallback(req, "unsupported_field");
    }

    const imageB64 = req.inputs.image_b64;
    if (typeof imageB64 !== "string" || !imageB64) {
      return this.fallback(req, "image_missing");
    }

    const startedAt = performance.now();

    try {
      const resp = await fetch(`${this.baseUrl}/api/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: this.model,
          prompt: CALLOUT_PROMPT,
          images: [imageB64],
          stream: false,
          options: { temperature: 0, top_p: 1, num_predict: 256 },
        }),
        signal: AbortSignal.timeout(this.timeoutMs),
      });

      if (!resp.ok) {
        return this.fallback(req, `http_${resp.status}`);
      }

      const data = (await resp.json()) as {
        response?: string;
        prompt_eval_count?: number;
        eval_count?: number;
      };
      const callouts = parseCallouts(data.response ?? "");
      const latencyMs = Math.round(performance.now() - startedAt);
      const confidence = scoreConfidence(callouts);

      const meta: EnrichmentResponse["meta"] = {
        provider: this.name,
        promptTemplateVer: PROMPT_TEMPLATE_VER,
        costUsd: 0,
        latencyMs,
        cacheHit: false,
      };
      if (data.prompt_eval_count !== undefined) meta.tokensIn = data.prompt_eval_count;
      if (data.eval_count !== undefined) meta.tokensOut = data.eval_count;

      return {
        id: req.id,
        field: req.field,
        result: callouts,
        confidence,
        meta,
      };
    } catch (err) {
      log.warn({ err }, "Ollama vision call failed");
      return this.fallback(req, "upstream_unreachable");
    }
  }

  private fallback(req: EnrichmentRequest, reason: string): EnrichmentResponse {
    log.debug({ reason }, "Ollama vision fallback");
    return {
      id: req.id,
      field: req.field,
      result: null,
      confidence: "low",
      meta: {
        provider: this.name,
        promptTemplateVer: PROMPT_TEMPLATE_VER,
        cacheHit: false,
        costUsd: 0,
      },
    };
  }
}

const CALLOUT_TOKEN_RE = /"([\d]+(?:-[\d]+[A-Z]?)?(?:\.\d+)?)"/g;

function parseCallouts(raw: string): string[] {
  if (!raw) return [];
  let s = raw.trim();
  if (s.startsWith("```")) {
    s = s.replace(/^```[a-zA-Z]*\n?/, "").replace(/\n?```$/, "");
  }
  try {
    const parsed = JSON.parse(s);
    if (Array.isArray(parsed)) {
      return parsed.map((x) => String(x).trim()).filter((x) => x.length > 0);
    }
  } catch {
    // fall through to regex
  }
  const matches = [...raw.matchAll(CALLOUT_TOKEN_RE)];
  return matches.map((m) => m[1]!);
}

function scoreConfidence(callouts: string[]): "high" | "medium" | "low" {
  if (callouts.length === 0) return "low";
  if (callouts.length >= 5) return "high";
  if (callouts.length >= 2) return "medium";
  return "low";
}
