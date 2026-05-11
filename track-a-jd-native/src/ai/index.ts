/**
 * Wires the configured LLM provider chain.
 *
 * Selection driven by env.LLM_PROVIDER. The result is always wrapped in
 * CachedLLMProvider so the cache benefit applies regardless of upstream.
 */
import { env } from "../lib/env.js";
import { resolve } from "node:path";
import { CachedLLMProvider } from "./providers/cached.provider.js";
import { MockProvider } from "./providers/mock.provider.js";
import { ClaudeCodeHandoffProvider } from "./providers/claude-code-handoff.provider.js";
import { OllamaProvider } from "./providers/ollama.provider.js";
import { AnthropicBatchProvider } from "./providers/anthropic-batch.provider.js";
import type { ILLMProvider } from "./provider.js";
import { logger } from "../lib/logger.js";

export function createLLMProvider(): ILLMProvider {
  const cachePath = resolve(env.LLM_CACHE_PATH);
  const upstream = pickUpstream();
  logger.info({ upstream: upstream.name, cachePath }, "LLM provider initialised");
  return new CachedLLMProvider(upstream, cachePath);
}

function pickUpstream(): ILLMProvider {
  switch (env.LLM_PROVIDER) {
    case "mock":
    case "cached":
      // 'cached' uses Mock as the upstream — useful when there's no cache
      // hit but we still want a deterministic, side-effect-free response.
      return new MockProvider();

    case "claude-code-handoff":
      return new ClaudeCodeHandoffProvider({
        tasksFile: resolve("../shared/handoff/translation_tasks.jsonl"),
        resultsFile: resolve("../shared/handoff/translation_results.jsonl"),
      });

    case "ollama":
      return new OllamaProvider({
        baseUrl: env.OLLAMA_URL,
        model: env.OLLAMA_MODEL,
      });

    case "anthropic":
      if (!env.ANTHROPIC_API_KEY) {
        logger.warn(
          "LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY missing — falling back to Mock",
        );
        return new MockProvider();
      }
      return new AnthropicBatchProvider({ apiKey: env.ANTHROPIC_API_KEY });

    case "gemini":
      // Gemini free tier has data-training TOS risk for production dealer data.
      // Stubbed by design (see ADR-007). Use Anthropic or Ollama in prod.
      logger.warn("LLM_PROVIDER=gemini intentionally stubbed (TOS risk); using Mock");
      return new MockProvider();

    default:
      return new MockProvider();
  }
}
