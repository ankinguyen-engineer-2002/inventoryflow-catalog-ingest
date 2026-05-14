# vision-mlx — Self-hosted Vision LLM (Qwen2.5-VL)

Local OCR/document understanding for schematic images. Runs entirely on Apple
Silicon via MLX — zero API cost, zero outbound network at inference time.

## Why this exists

The InventoryFlow catalog test ships ~1,586 schematic images inside the xlsx.
Each carries numbered part-callouts (mostly English, some Chinese). A vision
LLM is the practical path to OCR them at scale. Cloud APIs (OpenAI / Anthropic
Vision) would work but violate the zero-API-cost constraint, so this module
exists as the local fallback engine.

## Why Qwen2.5-VL-7B-Instruct-8bit (not 4bit, not the base FP16 repo)

| Variant | RAM weights | OCR quality | Status |
|---|---|---|---|
| `Qwen/Qwen2.5-VL-7B-Instruct` (PyTorch, BF16) | ~16 GB + huge KV via MPS | best | rejected — MPS RAM blowup |
| `mlx-community/...-bf16` | ~14 GB | best | overkill, RAM waste |
| `mlx-community/...-8bit` | **~8 GB** | **~1% loss** | ✅ chosen — OCR sweet spot |
| `mlx-community/...-4bit` | ~5 GB | ~5–8% loss on small text | rejected — labels too small |
| `mlx-community/...-6bit` | ~6 GB | ~2–3% loss | fine alternative |

## RAM budget (M1 Max 64GB)

| Component | RAM |
|---|---|
| Qwen2.5-VL 8-bit weights | ~8 GB |
| KV cache (4-bit, capped context) | ~1.5–3 GB |
| Python + Pillow + MLX runtime | ~2 GB |
| macOS overhead | ~10–12 GB |
| **Headroom remaining** | **~38–40 GB** |

## Optimization levers (tuned in `parser.py`)

1. **`max_pixels` cap** (`1024 * 28 * 28 ≈ 800K`) — biggest single lever. Each
   28×28 patch = 1 visual token, so doubling pixels doubles KV. 800K is enough
   for callout labels on the Kayo schematics.
2. **KV cache 4-bit quantization** (`kv_bits=4`) — cuts cache memory ~4× with
   negligible quality cost.
3. **`max_tokens=512`** — output is JSON list, never an essay.
4. **Image pre-resize** to longest-edge 1280 — predictable pixel budget.
5. **Single load, many calls** — `VisionParser` keeps the model in memory.

## Speed expectations

On M1 Max 64GB after warm-up:
- Per-image latency: ~5–12 s (depends on output length).
- 1,586 images sequentially: ~3–5 hours.
- Throughput could be ~2× with mlx-vlm continuous batching server
  (`mlx_vlm.server`) but adds complexity — defer until needed.

## What about CPU threads?

MLX runs on the Apple GPU via Metal. CPU threads only affect tokenization and
data loading, which are not the bottleneck. Bumping `OMP_NUM_THREADS` does
nothing useful here — the right lever is image preprocessing pipelining
(handled implicitly by Python's GIL + MLX's async eval).

## Files

- `parser.py` — `VisionParser` class with optimized config.
- `smoke_test.py` — single/few-image test that reports RAM + latency.
- `.venv/` — local Python 3.12 venv (gitignored).

## Run

```bash
cd shared/vision-mlx
source .venv/bin/activate

# Smoke test against extracted sample images
python smoke_test.py

# Or pass specific image paths
python smoke_test.py ../../sample-output/images/03a02ea*.png
```

## macOS GPU memory cap (optional)

The default Metal cap on a 64GB Mac is ~48 GB. Vision + Q8 + KV easily fits
without raising it. If you scale to multiple parallel models, bump with:

```bash
sudo sysctl iogpu.wired_limit_mb=57344   # 56 GB cap, resets at reboot
```
