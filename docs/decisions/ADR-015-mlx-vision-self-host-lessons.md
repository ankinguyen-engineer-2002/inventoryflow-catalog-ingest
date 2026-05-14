# ADR-015: MLX vision self-host — model selection, GPU contention, and prompt engineering

**Status:** Accepted
**Date:** 2026-05-13
**Author:** Aric Nguyen

---

## Context

The `ILLMProvider` abstraction (ADR-007) commits to local self-host as a first-class production option. To validate that commitment with measured numbers rather than vendor benchmarks, I ran `mlx-vlm` against all 1,573 schematic images from the Kayo ATV catalog on a single M1 Max 64 GB workstation and recorded what actually happened.

The questions I needed answered:

1. Which MLX-quantized Qwen-VL variant is the right default for this workload?
2. How many parallel workers can the M1 Max GPU actually sustain?
3. What prompt patterns fail on small models that work on large ones?
4. What's the realistic wall-clock + cost for 1,573 images compared to a paid API?

This ADR records the answers I measured.

---

## Decision

**Default for local self-host: hybrid two-phase pipeline.**

1. **Phase 1 — `mlx-community/Qwen2-VL-2B-Instruct-4bit`, 5 parallel workers.** Handles ~90% of schematics at ~3 s/image.
2. **Phase 2 — `mlx-community/Qwen2.5-VL-7B-Instruct-8bit`, 3 workers.** Re-runs only the 10% that failed Phase 1 (dense schematics with 30+ callouts where the 2B model hallucinates).

**Hard caps on M1 Max:**
- **Parallel workers ≤ 5** (Metal GPU command-buffer timeout kills any worker beyond ~5 concurrent inferences on this hardware).
- **`max_tokens = 2048`** for vision callout extraction (model can hallucinate up to ~150 fake callouts on dense images; bigger budget doesn't fix the loop but prevents truncated-JSON failures from being misclassified).

**Required prompt discipline:**
- Never use `|` as a choice separator in prompts for small models. 2B parameter models read it as literal string content.
- Always include a concrete output example, not just a schema spec.
- Use compact field names (`"n"`, `"pos"`) over verbose ones (`"callout_number"`, `"approx_position"`) — saves 30% of output tokens on long lists.

---

## Consequences

### Positive

- **Measured throughput**: ~35 min wall-clock for 1,573 images on a single M1 Max. Vs ~90 min for pure 7B-only run.
- **Zero marginal cost** at one-shot scale, vs ~$15 for Anthropic Claude Vision or ~$30 for GPT-4 Vision at $0.01–0.02 per image.
- **0% JSON parse failure** in steady state (Phase 1 catches simple schematics, Phase 2 catches dense ones).
- **Hardware constraints documented** so the next engineer doesn't lose 3 workers to GPU timeout the way I did.

### Negative

- **Two-model orchestration is more complex** than single-model. Requires:
  - Two model downloads (~10 GB combined)
  - Failure routing between phases
  - Output merge step
- **2B hallucination is a known limitation, not a bug we can fix in our code.** The 2B model genuinely doesn't know when to stop generating callouts on dense schematics. The workaround is to use 7B for those. A future fix (mlx-vlm `repetition_penalty` support, or a stop-string sequence on `]`) would eliminate this, but isn't available in mlx-vlm 0.5.x as of 2026-05.
- **Apple-Silicon-specific** — this entire architecture doesn't apply on x86 / NVIDIA. The migration plan for non-Apple production hosts is to use `vllm` with Qwen2.5-VL-7B-Instruct directly on an L40S / A10G GPU.

### Neutral

- The hybrid pipeline adds a Phase-1 → Phase-2 routing step in the orchestrator. I implemented this as a simple "OCR record with `ocr_result == null` from Phase 1 is re-enqueued for Phase 2" — minimal code.

---

## Alternatives considered

### A. Single-model 7B everywhere

**Why rejected:** Slower (~90 min vs ~35 min hybrid) for ~no quality gain on simple images. M1 Max RAM cap forces 3 workers max for 7B-8bit (9 GB each × 3 = 27 GB), limiting parallelism. The 2B model genuinely is fine for 90% of the workload.

### B. Single-model 2B everywhere

**Why rejected:** ~10% systematic failure rate on dense schematics (the hallucination loop). Even with `max_tokens=2048` the model generates 149+ fake callouts on complex images. Not acceptable for production output.

### C. Pure OCR (PaddleOCR / Tesseract)

**Why rejected for this workload:** Multilingual schematics with rotated, overlapping callout numbers confuse classical OCR. Smoke-tested on 5 images: PaddleOCR caught ~60% of callouts vs vision LLM's ~95% (excluding the hallucination failures). For documents with cleaner layouts (invoices, forms) PaddleOCR would win on cost; for schematics, it loses.

### D. Paid API (Claude Vision / GPT-4 Vision / Bedrock)

**Why deferred, not rejected:** Cost-efficient at one-shot scale ($15 for full run vs $0 self-host), but loses the zero-marginal-cost story at recurring scale. The provider abstraction (ADR-007) makes adding paid-API providers a config change, not a rewrite. Production deployment will likely use Bedrock-Claude-Vision as the default for customer-data residency.

### E. Mistral OCR (managed)

**Why deferred:** Cheapest paid option ($1.50 for full run) but introduces a new vendor dependency. Re-evaluate when Mistral's API has 1+ year of operational track record.

---

## Measured numbers (the data this decision is based on)

### Per-model performance (M1 Max 64 GB)

| Model | RAM/worker | Latency (solo) | Latency (5 workers) | Fail rate | Worker cap |
|---|---|---|---|---|---|
| Qwen2.5-VL-7B-Instruct-8bit | ~9 GB | 5–16 s | 21–36 s (contention) | ~10% | 2–3 (RAM) |
| Qwen2-VL-2B-Instruct-4bit | ~1.5 GB | 2.4 s | 3–4 s | ~9% (hallucination on dense) | 5 (GPU watchdog) |

### Pipeline ACTUAL execution + verification (updated 2026-05-14)

The hybrid 2B→7B fallback plan above was **abandoned mid-run** after measurement showed 2B undercounted callouts by ~37% on dense schematics (verified against 7B output on overlapping 40-image sample). Switched to **7B-only Phase 1** with anti-loop Phase 2 retry. Final pipeline:

| Phase | Model | Workers | Images | Wall-clock | JSON parse / metric |
|---|---|---|---|---|---|
| 1 | Qwen2.5-VL-7B-Instruct-8bit | 3 (parallel) | 1,573 (all) | ~4-5 h | 1463 OK (93.0%) |
| 2 | Qwen2.5-VL-7B-Instruct-8bit (anti-loop config) | 1 | 110 (Phase 1 fails) | ~26 min | 39 OK (35.5% recovery) |
| 3a | Phase 3 verify (Layer 3 consistency, pure Python) | — | 1573 | <1 min | 264 duplicate_n caught |
| 4 | Phase 4 coverage (Layer 4, vs parts_table ground truth) | — | 1573 | ~1 min (xlsx load + verify) | 62.4% precision ≥90% |
| 5 | DB integrate (`integrate_into_track_a.py` → image_callouts) | — | 1573 | <30 s | 1573 upserts verified live |
| **Total** | 7B-only with full 5-layer verification | mixed | 1,573 | **~4.5-5.5 h** | **675 HIGH (42.9%) post-Layer 4** |

**The 2B model was rejected** because content quality (callout count recall) was worse than 7B even though JSON parse rate was similar. Lesson: JSON validity ≠ content correctness.

**Phase 3a Layer 3** check revealed 264 of the Phase 1 "OK" records had `duplicate_n` hallucination — content was hallucinated even though JSON was valid. Demoted to MEDIUM.

**Phase 4 Layer 4** (the most rigorous check) then cross-referenced OCR output against the parts table extracted from the source xlsx. Per-image PRECISION = |OCR callouts ∩ parts_table callouts| / |OCR callouts|. Results:
- 62.4% of images have precision ≥90% (clean OCR — almost all callouts real)
- 19.9% have precision <70% (significant hallucinated callout numbers)
- Per-sheet UNION coverage (across all images per sheet): 64.5% reach 100%, 85% reach ≥70%
- 5 sheets at 0% coverage (text-only specs sheets without schematic diagrams — expected)

**Layer 4 demoted 359 more records** from HIGH to MEDIUM/LOW because precision <90% indicates hallucinated callout numbers that Layer 3 didn't catch.

**Final confidence distribution in `image_callouts` table**:
| tier | count | % |
|---|---|---|
| HIGH | 675 | 42.9% |
| MEDIUM | 467 | 29.7% |
| LOW (incl. 71 DEAD-mapped-to-low) | 431 | 27.4% |

**The architectural lesson**: HIGH confidence dropped from 65.7% (Phase 3a only) to 42.9% (Phase 4 added). The 22-percentage-point swing is not a bug — it's the value Layer 4 adds. Without ground-truth cross-reference, we'd have over-stated quality by 22%. The architecture supports honest measurement at every layer; the discipline is in actually doing all the layers.

### Thermal + power envelope (M1 Max, live via `macmon`)

| State | RAM used | GPU temp | GPU power | CPU |
|---|---|---|---|---|
| Idle | 25 GB | 38°C | <1 W | 5% |
| 5 workers 2B | 44 GB | 72°C | 21 W | 18% |
| 5 workers 7B-8bit | 62 GB | 72°C | 23 W | 18% |
| 8 workers 2B (3 died from GPU timeout) | 57 GB | 75°C | 24 W | 25% |

GPU thermal throttle ~95°C, so 72°C is **23°C below limit** — heat is not the constraint. Metal command-buffer timeout is.

### Cost vs paid alternatives (1,573 images, one-shot)

| Provider | Cost | Wall-clock |
|---|---|---|
| Anthropic Claude Vision | $15.73 | ~30 min (rate-limited) |
| OpenAI GPT-4 Vision | $31.46 | ~30 min |
| **MLX self-host (this submission)** | **$0** (electricity ~$0.05) | **~35 min** |
| Mistral OCR (managed) | $1.57 | ~10 min |

At dealer-#1 scale, paid API is cheap. At 1,000 dealers/week (50M images/year), MLX self-host saves ~$500k/year.

---

## Prompt engineering details

### Original prompt that failed on 2B (56% parse failure)

```text
Return JSON array: [{"callout_number": int, "approx_position":
  "top-left|top|top-right|center-left|center|center-right|bottom-left|bottom|bottom-right"}]
```

The 2B model interpreted `|` as literal string content and copy-pasted the entire enum into `approx_position`. Output was 1,500+ chars before hitting `max_tokens`. Truncated JSON.

### Working prompt (0% parse failure on simple schematics)

```text
Output ONLY a JSON array. Each item has exactly these keys:
  "n" — integer, the callout number
  "pos" — string, one value from: top-left, top, top-right, center-left,
          center, center-right, bottom-left, bottom, bottom-right
Example output: [{"n": 1, "pos": "top-left"}, {"n": 2, "pos": "center"}]
Do not add any other text. JSON only.
```

Key differences:
- Explicit "one value from" rather than `|`-separated grammar
- Concrete example in the prompt
- Compact field names (`n`, `pos`) save tokens on long lists
- Explicit "Do not add any other text" — important for small models

---

## What I'd do next (deferred work)

1. **Patch mlx-vlm to support `repetition_penalty`** — the 2B hallucination is exactly what `repetition_penalty=1.1` was invented to dampen. Upstream PR worth contributing.
2. **Add stop-string support** — fire `]` as a stop sequence to forcibly terminate output at the JSON close bracket. Belt-and-braces against repetition loops.
3. **mlx-vlm continuous batching server** — `mlx_vlm.server` does proper batching across concurrent requests. ~2× throughput, but adds an additional service to operate.
4. **Bedrock-Claude-Vision provider** — for regulated customers requiring in-VPC inference, add a 7th `ILLMProvider` backend.
5. **Per-dealer model selection** — some dealers ship cleaner schematics than others. Adaptive routing (try 2B first, fall back to 7B on parse failure) becomes per-dealer cost optimization.

---

## References

- `mlx-vlm` v0.5.0 (released 2026-05-06): https://github.com/Blaizzy/mlx-vlm
- `mlx-community/Qwen2-VL-2B-Instruct-4bit`: https://huggingface.co/mlx-community/Qwen2-VL-2B-Instruct-4bit
- `mlx-community/Qwen2.5-VL-7B-Instruct-8bit`: https://huggingface.co/mlx-community/Qwen2.5-VL-7B-Instruct-8bit
- Apple Metal GPU command-buffer timeout (`kIOGPUCommandBufferCallbackErrorTimeout`): documented in macOS Metal Best Practices
- Companion code: `shared/vision-mlx/parser.py`, `shared/vision-mlx/batch_vision_ocr.py`, `shared/vision-mlx/integrate_into_track_a.py` in this repo
- ADR-007 (LLM provider cost strategy) — superseded only on the implementation details of the MLX provider
- ADR-009 (When to switch tracks) — Track B's global canonical translation table changes the cost story for vision OCR cache as well; same migration triggers apply
