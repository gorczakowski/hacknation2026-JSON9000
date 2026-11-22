# Candidate models

## EU-compliant API hosts found (2026-11-21)
| Host | Company / servers | Relevant models (API ID) | API | Notes |
|---|---|---|---|---|
| **Mistral** | Mistral AI SA, Paris. **Use `api.eu.mistral.ai` for the submission**; the global `api.mistral.ai` makes no location promise | `ministral-3b-2512`, `ministral-8b-2512`, `ministral-14b-2512` (Apache-2.0, vision, 256k context) | OpenAI-compatible at `/openai/v1` (preview); structured output supported | $0.10 / $0.15 / $0.20 per M tokens (+10% on the EU endpoint). Trains on data unless opted out; zero data retention available on pay-as-you-go. **Mistral-Small-3.2 (24B) retired in July 2026**; Small 4 is a 119B MoE. |
| **ARK Labs** | Ark Sp. z o.o., Szczecin (PL). "Inference runs on GPU servers ARK operates in the EU" | `arklabsai/Qwen3.5-4B`, `arklabsai/Bielik-11B-v3.0-Instruct`, `arklabsai/Bielik-11B-v3.1-Instruct`, `arklabsai/Qwen3.6-35B-A3B`, `arklabsai/Qwen3.5-122B-A10B` | OpenAI-compatible, base `https://api.ark-labs.cloud/api/v1`; `json_object` / `json_schema` accepted (guided, not constrained) | Prompts not stored or logged, no training. Qwen3.5-4B $0.0097 / $0.33; Bielik v3.0 $0.10 / $0.40. Starter credits with Google sign-in. Key is in `.env` as `ARK_API_KEY`. |
| **IONOS AI Model Hub** | IONOS (DE), endpoint `openai.inference.de-txl.ionos.com` | `mistralai/Mistral-Small-24B-Instruct` (3.2), `Qwen/Qwen3.5-9B` | OpenAI-compatible | Needs an IONOS account; prices not read |
| STACKIT (DE) | Schwarz group | none of our candidates (Qwen3.8-27B, Gemma-4-31B, gpt-oss-20B/120B) | OpenAI-compatible | gpt-oss-20B is there (21B) |
| Scaleway, OVH, Nebius, Regolo | EU | not verified (pages blocked or JS-rendered) | | |

Not EU-compliant (experiments only): OpenRouter (US), Cloudflare/Clairic, the OpenRouter decision models (Liquid, Baseten, etc.).

## Decision-model families (typed choices / yes-no with probabilities; not for free-text extraction)
- **basal** (Remigiusz Kinas, PL; https://basal.si5.pl, Apache-2.0):
  - Models: basal-2.0-mini (on Bielik-1.5B, ~2B), basal-2.0 (on Bielik-4.5B, ~5B), basal-2.0-max (11B), plus the omni line (Gemma-4 base).
  - Self-host only (`basal-serve`, GPU/vLLM, or GGUF on CPU). No hosted API.
  - Its experimental `extract` task copies spans from the document, but was only benchmarked on the 12B omni model (90.1% on synthetic invoices).
- **Bielik-One** (SpeakLeash):
  - Four decision models (11B v3.1, 11B v3.0, 7B v3.0, 4B on Qwen3.5-4B).
  - Self-host on GPU with vLLM plus the `bielik-one` package. No API.
- Both could only replace our keyword rules for **classification and the 1–12 code**. Those rules are already 100% on the text set, and OpenRouter decision models get 90–100% on the action passage.

Checked 2026-11-21. Sources:
- **Size** and **licence**: exact parameter counts and licences from the Hugging Face API.
- **Price**: OpenRouter's public model list, in USD per 1M tokens (input / output).
- **Clairic**: Cloudflare Workers AI docs.

How to read the table:
- **The Pts columns below are OUTDATED.** They used the earlier mentor rule (−1 per 1B). The official rule (README, 11-21 18:46, see FAQ.md) is:
  - points depend on the **average total** parameters of all LLMs used; MoE counts total, not active
  - ≤8B = 20, >8–14B = 18, >14–32B = 12, >32–70B = 8, >70–150B = 4, >150B = 0
  - models run only locally on the test machine = 20
  - OCR via API counts like an LLM
- What that means for this list:
  - every model up to 8B (LFM-1.7B, schematron, Ministral-3B, Gemma-3-4B, Ling-tiny 7.9B) = 20
  - Ministral-8B (8.9B), Granite-8B (8.8B), Qwen3.5-9B, Clairic-flash ≈ 18
  - Ministral-14B (13.9B) = 18
  - 24–32B models, including the MoEs gemma-4-26b-a4b, Qwen3.6-35B (36B → 8!) and Nemotron-30B-A3B, mostly = 12
  - Clairic (27B) = 12
- **img** means the model accepts page images, so it can read scans directly.
- Our rule for EU hosting doesn't apply here. This is the list for experiments; providers come later.

## Tiny (≤ 1B, full points): **none on OpenRouter**
These would need a host other than OpenRouter. I found them from memory and haven't checked them, so verify availability and licences before relying on them:
- **LiquidAI LFM2-350M-Extract / LFM2-1.2B-Extract**: small models built specifically for document → JSON extraction.
- **NuExtract-tiny** (NuMind, 0.5B): an extraction model. Newer NuExtract 2.0 versions are 2B/4B.
- **Qwen3-0.6B**, **Gemma-3-1B / 270M**: general tiny models that would need fine-tuning or very tight prompts.
- **Not LLMs at all:**
  - **GLiNER** (≈200–500M): finds names and organizations without training examples.
  - **HerBERT** (Polish BERT, 125M / 350M): can be fine-tuned to tag the fields we want.
  - Both run on CPU, but we must check whether running a model locally is allowed.

## Small (1–10B, about 11–19 pts)
| Model (OpenRouter id) | Size | Licence | $ in / out | img | Released | Pts |
|---|---|---|---|---|---|---|
| `liquid/lfm-3-1.7b` (has a `:free` variant) | 1.7B | other (LFM) | 0.08 / 0.20 | | 2026-11 | 19 |
| `inference-net/schematron-v2-turbo` (an extraction fine-tune of Granite-4.0-H-Micro) | ~3B | ? | 0.03 / 0.15 | | 2026-09 | ~17 |
| `inference-net/schematron-v2-small` (an extraction fine-tune of Llama-3.2-3B) | 3.2B | llama3.2 | 0.05 / 0.23 | | 2026-09 | 17 |
| `mistralai/ministral-3b-2512` | 3.8B | Apache-2.0 | 0.10 / 0.10 | img | 2025-12 | 17 |
| `google/gemma-3-4b-it` | 4.3B | Gemma | 0.05 / 0.10 | img | 2025-03 | 16 |
| `inclusionai/ling-3.0-tiny` | 7.9B | MIT | 0.015 / 0.045 | | 2026-08 | 13 |
| `ibm-granite/granite-4.2-8b` | 8.8B | Apache-2.0 | 0.06 / 0.15 | | 2026-08 | 12 |
| `mistralai/ministral-8b-2512` | 8.9B | Apache-2.0 | 0.15 / 0.15 | img | 2025-12 | 12 |
| `qwen/qwen3-vl-8b-instruct` | 8.8B | Apache-2.0 | 0.25 / 0.75 | img | 2025-10 | 12 |
| `moondream/moondream3.1-9b-a2b` (vision/OCR-focused MoE) | 9.3B (2B active) | other | 0.30 / 2.50 | img | 2026-10 | 11 (19 if active) |
| `qwen/qwen3.5-9b` | 9.7B | Apache-2.0 | 0.17 / 0.25 | img | 2026-03 | 11 |
| **Clairic-flash** (Workers AI `@cf/cloudflare/clairic-flash`, built on Qwen3.5-9B) | ~9.7B | Apache-2.0 | 0.10 / free output | img/PDF | 2026-11 | 11 |

## Medium (12–36B, about 1–9 pts on total size)
| Model | Size | Licence | $ in / out | img | Released | Pts (total / active) |
|---|---|---|---|---|---|---|
| `mistralai/ministral-14b-2512` | 13.9B | Apache-2.0 | 0.20 / 0.20 | img | 2025-12 | 7 |
| `openai/gpt-oss-20b` | 20.9B (3.6B active) | Apache-2.0 | 0.017 / 0.085 | | 2025-08 | 1 / 17 |
| `mistralai/mistral-small-3.2-24b-instruct` (current naive default) | 24.0B | Apache-2.0 | 0.075 / 0.20 | img | 2025-06 | 1 |
| `liquid/lfm-2.5-24b-a2b` | 24.0B (2B active) | other | 0.05 / 0.70 | | 2026-10 | 1 / 19 |
| `google/gemma-4-26b-a4b-it` (has `:free`) | 25.8B (4B active) | Apache-2.0 | 0.068 / 0.225 | img | 2026-04 | 1 / 17 |
| `thinkingmachines/inkling-2-mini` | 26.4B | Apache-2.0 | 0.10 / 0.40 | img | 2026-11-21 | 1 |
| `qwen/qwen3.8-27b` | 27.8B | Apache-2.0 | 0.06 / 1.96 | img | 2026-08 | 1 |
| **Clairic** (Workers AI `@cf/cloudflare/clairic`, built on Qwen3.8-27B) | ~27.8B | Apache-2.0 | 0.10 (promo until 12-01, then 0.24) / free output | img/PDF | 2026-11 | 1 |
| `nvidia/nemotron-3.5-lightning` (has `:free`) | 31.6B (3B active) | NVIDIA open | 0.07 / 0.20 | | 2026-08 | 1 / 18 |
| `z-ai/glm-4.7-flash` | 31.2B (~3B active) | MIT | 0.061 / 0.40 | | 2026-01 | 1 / ~18 |
| `google/gemma-4-31b-it` (has `:free`) | 31.3B | Apache-2.0 | 0.20 / 0.40 | img | 2026-04 | 1 |
| `qwen/qwen3.6-35b-a3b` | 36.0B (3B active) | Apache-2.0 | 0.10 / 0.90 | img | 2026-04 | 1 / 18 |

## Big models, as an upper bound on quality (1 pt)
| Model | Size | Licence | $ in / out | img |
|---|---|---|---|---|
| `z-ai/glm-5.3-flash` | 321B | MIT | 0.04 / 0.08 | img |
| `deepseek/deepseek-v4.1-flash` | 763B | MIT | 0.05 / 0.20 | img |
| `qwen/qwen3.5-397b-a17b` | 403B (17B active) | Apache-2.0 | 0.45 / 3.00 | img |

## Notes
- **Cost is negligible.** The whole training set (≈230 PDFs, roughly 1–3k tokens of text each) is about 0.5M input tokens. That's cents even for medium models. Scans sent as images cost more (≈1–2k tokens per page image).
- **No model covers both "tiny" and "reads scans".** For scans, a tiny model would need OCR in front of it (MinerU basic, RapidOCR).
- Not included: closed models (qwen-plus/max, mistral-medium), translation models (Hy-MT), and roleplay fine-tunes.
