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
    case "gemini":
    case "anthropic":
      // These are documented production targets (see ADR-007) but not
      // wired in this submission to keep the reviewer experience zero-cost.
      logger.warn(
        { provider: env.LLM_PROVIDER },
        "provider stubbed — falling back to Mock to avoid surprise API calls",
      );
      return new MockProvider();

    default:
      return new MockProvider();
  }
}
