# ADR-007: LLM provider abstraction + zero-API-key submission

## Status
Accepted — 2026-05-11

## Context

The test asks for AI tooling demonstration ("Vision LLMs (OpenAI, Claude)"). Two constraints:

1. **I (the candidate) will not pay for raw API access** for this take-home. I subscribe to Claude Max Team and ChatGPT Codex Enterprise — those cover my workflow but not arbitrary programmatic API access.
2. **The reviewer should be able to run the submission without entering any API key** or being asked to pay for inference.

If LLM is only callable via paid API, both constraints are violated. The architecture needs to make AI usage **independent of any single provider** and **runnable cold from cache**.

The good news: this is **also the right production pattern** for InventoryFlow at scale. When LLM cost is 30%+ of the cloud bill (likely at 500+ dealers), provider abstraction + global cache + Batch API economics is exactly what they need. The "cheap submission" and "production-correct architecture" converge.

## Decision

Build an `ILLMProvider` TypeScript interface with **five implementations**, all switchable via env var, wrapped in a **cache decorator** backed by SQLite committed to the repo.

```ts
// src/ai/provider.ts
export interface ILLMProvider {
  readonly name: string;
  translateCnToEn(cn: string, context?: PartContext): Promise<string>;
  extractCalloutsFromImage(imagePath: string): Promise<number[]>;
  inferMakeFromModelCode(modelCode: string): Promise<string>;
}
```

Implementations:

| Provider name           | File                                          | Behaviour                                              |
|-------------------------|-----------------------------------------------|--------------------------------------------------------|
| `mock`                  | `providers/mock.provider.ts`                  | Fixture lookup, deterministic                          |
| `cached` (decorator)    | `providers/cached.provider.ts`                | Wraps any upstream; SQLite cache keyed by SHA-256(prompt+image) |
| `claude-code-handoff`   | `providers/claude-code-handoff.provider.ts`   | Writes tasks.json, blocks for human-run Claude session, reads results.json |
| `ollama-local`          | `providers/ollama.provider.ts`                | HTTP to local `ollama serve`, default model `qwen2-vl:7b` (CN-strong) |
| `gemini-free-tier`      | `providers/gemini-free.provider.ts`           | Google Gemini 2.0 Flash free tier (15 req/min)         |
| `anthropic-batch`       | `providers/anthropic-batch.provider.ts`       | **Production target**, never invoked in submission     |

Wiring at runtime:

```ts
const upstream = match(env.LLM_PROVIDER)
  .with('mock', () => new MockProvider())
  .with('claude-code-handoff', () => new ClaudeCodeHandoffProvider())
  .with('ollama', () => new OllamaProvider(env.OLLAMA_URL))
  .with('gemini', () => new GeminiFreeProvider(env.GEMINI_API_KEY))
  .with('anthropic', () => new AnthropicBatchProvider(env.ANTHROPIC_API_KEY))
  .exhaustive();

export const llm = new CachedLLMProvider(upstream, sqliteCache);
```

**Cache file** lives at `shared/llm-cache.sqlite` and is committed to the repo. Every call is `(prompt_sha256, image_sha256?, response_json, cost, latency, model_version, prompt_template_version)`.

**Cache hit means zero upstream call**. Reviewer running cold from cache pays $0 and waits zero LLM latency.

## AI suggestion vs my override

**Claude initially suggested** wiring directly to the Anthropic SDK with a clean `AnthropicProvider` class and ".gitignore the env file".

**I overrode** because:

1. **Reviewer can't run it** without entering my API key (or theirs). That's a hard fail for a take-home.
2. **Even if reviewer had a key**, charging them per `pnpm ingest` is bad UX.
3. **Single-provider lock-in is wrong production design**. At 500+ dealers, switching to Anthropic Batch (50% cost) or to Gemini for analytics is a config change, not a refactor. Build the seam day-one.
4. **The provider abstraction is the architectural payload**, not the specific provider. Showing five providers + a cache decorator demonstrates depth; showing one provider demonstrates fluency in one SDK.
5. **Audit log** (`ingest_audit` table) captures every call — including cache hits, with `cache_hit=true`. This is the same audit pattern needed for cost attribution at scale.

## How the submission is generated

```bash
# 1. I run with handoff provider
LLM_PROVIDER=claude-code-handoff pnpm ingest <file>
# → writes shared/handoff/translation_tasks.json with ~200 tasks

# 2. I open Claude Code (in my Max plan) and prompt:
#    "Read shared/handoff/translation_tasks.json. For each entry,
#     translate the CN field to English. Output shared/handoff/
#     translation_results.json with id+result pairs."

# 3. Re-run ingest; provider reads results.json and caches them
LLM_PROVIDER=claude-code-handoff pnpm ingest <file>

# 4. Commit shared/llm-cache.sqlite
git add shared/llm-cache.sqlite && git commit -m "chore(cache): seed LLM cache"
```

The reviewer then runs `pnpm ingest` with **`LLM_PROVIDER=cached`** (default) — all calls hit the SQLite cache, no upstream.

## Trade-offs accepted

- **Submission requires manual cache-generation step on my side** — documented in runbook. Not an issue for one submission; would be in CI.
- **Cache invalidation strategy**: prompts are versioned (`prompt_template_version` in cache key). Bumping the template invalidates entries — intentional. Cache is regenerated.
- **Mock provider drift**: fixtures may diverge from real provider responses. Mitigated by unit tests that compare mock vs real on known inputs (one-time, when regenerating cache).
- **Audit table can leak sensitive prompt content** if dealer files contain PII. Mitigated: prompts are templated; no row-level data goes into prompts beyond the CN string being translated. PII scanner runs on `ingest_audit.response_text` before commit.

## When to revisit

- When dealer count > 200, switch primary provider to `anthropic-batch` (50% cost vs realtime) for production, keep `claude-code-handoff` for dev.
- When Gemini free tier hits 1500 req/day limit consistently, drop it from rotation.
- If `qwen2-vl` quality drops below acceptance threshold for CN content, evaluate Qwen2.5-VL 72B (cloud, paid).

## Sources

- Anthropic Batch API docs: https://docs.anthropic.com/en/docs/build-with-claude/batch-processing (retrieved 2026-05-11).
- Anthropic Prompt Caching: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching (retrieved 2026-05-11).
- Qwen2-VL paper (Bai et al., 2024) — strongest open-source multilingual VLM as of 2026-Q1.
- Google Gemini 2.0 Flash pricing/free-tier: https://ai.google.dev/pricing (retrieved 2026-05-11).
- Ollama supported models: https://ollama.com/library (retrieved 2026-05-11).
- Pattern reference: Vercel AI SDK provider abstraction; LangChain LLM wrappers (both prove the abstraction is industry-standard).
