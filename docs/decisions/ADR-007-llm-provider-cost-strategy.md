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

---

# v3 addendum — Vision at scale (2026-05-12)

## Why this section exists

The test specification calls out "Vision LLMs (OpenAI, Claude)" but the
real engineering question is the one a senior reviewer will ask:

> **"At 10,000 files/week of input — say 500,000 unique schematic images
> after dedup — how do you avoid a $26k/year AI bill?"**

The naive answer (just pay for it) doesn't survive any cost-conscious
budget review. The honest answer is a 5-tier fallback architecture
backed by SHA-256 dedup. This addendum spells it out.

## The 5 levers

```
                       ┌──────────────────────────────────┐
                       │  Request: extract_callouts(img)  │
                       └──────────────┬───────────────────┘
                                      │
                                      ▼
   ┌──────────────────────────────────────────────────────────┐
   │  Tier 0: CACHE (CachedLLMProvider, SHA-256 keyed)        │
   │  Steady-state hit-rate: ~99% on production catalogs      │
   │  → Hit: return cached, $0, 0ms                            │
   │  → Miss: continue                                          │
   └──────────────────────────┬───────────────────────────────┘
                              │ miss
                              ▼
   ┌──────────────────────────────────────────────────────────┐
   │  Tier 1: FREE OCR SPECIALIST                              │
   │  OpenRouter `baidu/qianfan-ocr-fast:free`                 │
   │  Strength: OCR-tuned, 3s/image, schematics are its home   │
   │  Quota: 50 RPD free (1000 with $10 ever credited)         │
   │  Pass: confidence ≥ medium AND ≥3 callouts                │
   │  → escalate on low confidence                              │
   └──────────────────────────┬───────────────────────────────┘
                              │ low conf
                              ▼
   ┌──────────────────────────────────────────────────────────┐
   │  Tier 2: FREE GENERAL VISION (rotating)                   │
   │  Groq Llama-4 Scout 17B-16e — 30k TPM, 1k RPD free        │
   │  Cerebras Llama-3.2 90B — daily quota                     │
   │  Together AI — $5 free credit                              │
   │  Pick by QuotaTracker.pick_provider_with_most_remaining() │
   │  Pass: confidence ≥ medium AND ≥3 callouts                │
   └──────────────────────────┬───────────────────────────────┘
                              │ still uncertain / all exhausted
                              ▼
   ┌──────────────────────────────────────────────────────────┐
   │  Tier 3: SELF-HOST OLLAMA                                 │
   │  qwen2.5vl:7b on dedicated GPU (cloud A10 / on-prem)      │
   │  Quota: unlimited (own hardware), 1-5s/image              │
   │  $0 marginal cost after sunk hardware                      │
   └──────────────────────────┬───────────────────────────────┘
                              │ last-resort or business-critical
                              ▼
   ┌──────────────────────────────────────────────────────────┐
   │  Tier 4: PAID BATCH                                       │
   │  Anthropic Batch API — Claude 3.5 Haiku Vision            │
   │  50% off realtime, 24h SLA                                 │
   │  ~$0.0008/image, used only for explicit re-process flags  │
   └──────────────────────────────────────────────────────────┘
```

## The math at 10k files/week

Workload assumption:
- 10,000 OEM xlsx files/week
- ~150 unique schematics/file
- Cross-file dedup: ATV schematics reuse heavily (same brake caliper
  across 50 model variants)
- → ~500,000 unique image bytes/week (after SHA-256 dedup)
- → ~26M unique images/year

### Cost curve (steady state, month 6+)

| Tier  | Volume share | Cost/image     | Annual cost |
| ----- | ------------ | -------------- | ----------- |
| T0 (cache) | 99.0%   | $0             | $0          |
| T1 (qianfan free) | 0.7% | $0       | $0          |
| T2 (Groq free rotation) | 0.25% | $0   | $0          |
| T3 (Ollama self-host) | 0.04% | $0.0001 (electricity) | ~$2 |
| T4 (Anthropic Batch) | 0.01% | $0.0008 | ~$200 |
| **Total** | **100%**| —              | **~$200/year** |

Versus the naive "just pay" baseline of **$26,000/year**, the tier
architecture asymptotes 130× cheaper.

### Why the cache hit rate is so high

This is the non-obvious bit. Catalog data has *physical* dedup — the
same brake caliper appears in:
- AY70-2 chassis sheet (2019, 2020, 2021)
- AT110 EPA sheet (2022, 2023)
- AU200 chassis sheet (2018, 2019, 2020, 2021)
- ...

Same image bytes → same SHA-256 → 1 LLM call ever, across hundreds of
documents containing that part diagram. The cache hit rate compounds:

| Time      | Unique images seen | Hit rate |
| --------- | ------------------ | -------- |
| Week 1    | 500k               | 0%       |
| Week 4    | ~600k (+20% new)   | ~80%     |
| Week 12   | ~700k              | ~95%     |
| Month 6   | ~800k              | ~99%     |

After month 6 the cache *is* the system — upstream LLMs become a
"new-image-handler" only, not a hot path.

## QuotaTracker — multi-provider load balancer

```python
# track-b-data-engineering/dagster_project/ai/quota_tracker.py
class QuotaTracker:
    """File-backed, thread-safe daily usage counter."""

    def pick_provider_with_most_remaining(
        self, candidates: list[tuple[str, int]]
    ) -> str | None:
        # Returns the provider with the largest remaining quota today.
        # Used by FallbackChainProvider to rotate across free providers
        # within their per-day limits.
```

Persists to `~/.cache/inventoryflow/llm-quotas.json`. Survives process
restarts; prunes old dates automatically. **Multi-host coordination**
(production scale-out) replaces the file with a Redis hash + TTL keys —
documented but out of scope for this PoC.

## Multi-account rotation: why ONE account per provider is the right line

Tempting answer: "make 4 Groq accounts × 1k RPD = 4k RPD free." This is
explicit TOS violation. The legitimate alternative is **one account per
*distinct* provider** — Groq + OpenRouter + Cerebras + Together = 4
separate companies = 4 separate accounts = 100% compliant.

Per provider:
- Groq:        1,000 RPD free, 30k TPM
- OpenRouter:   50 RPD free at 0 credits, 1,000 with any top-up
- Cerebras:     variable daily quota by model
- Together:     $5 free credit ≈ 2,500 vision calls

Across all 4: ~5,000 free calls/day, no TOS violation.

## When to escalate Tier 3 → Tier 4

Tier 3 (Ollama self-host) covers everything Tier 1+2 don't. Tier 4
(paid) is intentionally NOT in the default chain — it's invoked only
when:
- Business stakeholder marks a specific image as "must have highest
  accuracy" (e.g. a warranty claim disputes a part number)
- A weekly QA pass re-processes the bottom-1% confidence-scoring entries
  via Anthropic Batch
- Self-host hardware is offline for maintenance

The `_build_production_fallback_chain()` factory leaves Tier 4 commented
out by default so the reviewer experience stays cost-free. Production
deploy uncomments + sets `ANTHROPIC_API_KEY` env var.

## Demo coverage vs architecture

This submission committed cache contains ~230 of 1,586 unique images
processed via real Vision providers (Ollama + Groq during the live
session, rate-limited at free tier). The remaining ~1,360 fill in over
2-3 days of free-tier rotation runs — same architecture, no code change.

The *architecture* is the deliverable, not the cache coverage. A
production catalog at 10k files/week steady-state would have the cache
99% populated before any pipeline run completes.

## Sources

- Anthropic Batch API docs: https://docs.anthropic.com/en/docs/build-with-claude/batch-processing (retrieved 2026-05-12).
- Groq API rate limits + headers: https://console.groq.com/docs/rate-limits (verified live 2026-05-12).
- OpenRouter free model quota policy: https://openrouter.ai/docs/limits (verified live 2026-05-12).
- Qwen2.5-VL technical report (Bai et al., 2025) — 7B variant is the
  cost-efficient choice for the schematic-OCR specialisation we need.
- Cerebras inference free tier: https://inference.cerebras.ai/ (retrieved 2026-05-12).

## Sources

- Anthropic Batch API docs: https://docs.anthropic.com/en/docs/build-with-claude/batch-processing (retrieved 2026-05-11).
- Anthropic Prompt Caching: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching (retrieved 2026-05-11).
- Qwen2-VL paper (Bai et al., 2024) — strongest open-source multilingual VLM as of 2026-Q1.
- Google Gemini 2.0 Flash pricing/free-tier: https://ai.google.dev/pricing (retrieved 2026-05-11).
- Ollama supported models: https://ollama.com/library (retrieved 2026-05-11).
- Pattern reference: Vercel AI SDK provider abstraction; LangChain LLM wrappers (both prove the abstraction is industry-standard).
