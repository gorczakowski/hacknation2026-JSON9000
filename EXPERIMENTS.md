# Experiments log

All scores come from `python -m eval.score`, using the organizers' TP/FP/FN rules (see FAQ.md). Strict and lenient modes gave the same numbers so far. The dataset is `hacknation-2026/dane` at commit `84cc976`, 226 PDFs:
- 180 text PDFs (60 cases)
- 4 unrelated PDFs inside case folders
- 30 image-only scans
- 12 negative documents

## Full pipelines (2026-11-21)

| Run | What | F1 | Text F1 | Scans F1 | Classification | Cost |
|---|---|---|---|---|---|---|
| `naive_mistral24` | chat LLM, whole document, Mistral-Small-3.2-24B; scans sent as images | 0.962 | 0.966 | 0.932 | 226/226 | $0.10 |
| `naive_mistral24_norm` | same, plus format rules (BZP without space, TED → NNNNNN-YYYY, prowadzący null when there's one Zamawiający, abs(kwota)) | **0.977** | 0.981 | 0.953 | 226/226 | – |
| `s1_clairic_d12` | Liquid d1.2 classifies, Clairic (27B) extracts, d1.2 picks the 1–12 code from the full text | 0.948 | 0.950 | 0.936 | 226/226 | $1.04 |
| `s1_clairic_d12_norm` | same, plus format rules | 0.961 | 0.962 | 0.954 | 226/226 | – |

Main error sources:
- **The LLM invents `data` and `miejscowosc`** when the appeal has no dated header line. The gold is null in 15 such cases. Clairic takes "Warszawa" from KIO's address.
- **Mistral truncates account numbers** that wrap across lines (34 FP). Clairic copies spans exactly, and on przelew documents scores 0.995 vs 0.957.
- **Clairic returns "Name1, r. pr. Name2"** for `pelnomocnik.imie_nazwisko`. The gold keeps only the first attorney.
- **The 1–12 code (`czynnosc_powod_id`):** Mistral gets 80%, and d1.2 on the full text gets 50% (it is distracted by the generic "Zarzut nr …" sections about the OPZ).
- **Gold quirks:**
  - `mocodawca.nazwa` includes the owner's name for sole traders ("DUDEK-TECH Jarosław Dudek").
  - The company form keeps its trailing dot ("sp. j.").

### Rules-only main pipeline (`python -m odwolania2json`, config in `config/`), no model
| Version | F1 | Text F1 | Scans F1 | Precision | Classification | Time |
|---|---|---|---|---|---|---|
| v1 (2026-11-21) | **0.924** | 0.964 | 0.564 | 0.965 | 213/226 (13 misses are OCR'd scans: 7 appeals, 6 powers of attorney) | 3 s for 226 PDFs (scans read from the OCR cache) |
- By type: odwolanie 0.937 (P 0.989), pelnomocnictwo 0.854, przelew 0.964.
- Weak fields: `mocodawca.nazwa` (0.70), `pelnomocnicy.imie_nazwisko` (0.75), signatories (0.84).
- The header date and city are never invented (precision 1.000).

| v2: classification and sections ignore diacritics; OCR-tolerant patterns (`pe\wnomocn`); ruling signals anchored to line start | **0.950** | 0.964 | **0.855** | 0.957 | 223/226 (3 scanned powers of attorney missed) | 6 s |

| v3 (11-21 late): review fixes. Classifier applies each type's threshold before ranking; a method whose section is missing is skipped; per-page OCR with read errors reported; accounts keep the "PL" prefix (scorer ignores the prefix against the training gold) | **0.952** | 0.964 | 0.869 | 0.965 | 223/226 | 3.6 s |

| v4 (11-22): any-country IBANs (DE/FR/GB…, length checked per country, unknown country codes rejected); passage patterns for the 1–12 code moved into `odwolanie.yaml`; POA heading accepts "PEŁNOMOCNICTWO PROCESOWE" etc. (found with the new `calibrate` tool) | **0.956** | 0.968 | 0.869 | 0.965 | **226/226** | 7 s |

| **Hybrid in the CLI** (11-22): rules + Qwen3.5-4B (ARK), classification safety net, `fill` / `model_first` modes, sanity checks on model values, 1–12 fallback on the passage. OCR from the cache | **0.979** | – | 0.920 | 0.976 | 226/226 | 564 s (226 PDFs, 185 model calls; 36 failed during an ARK outage, before the retry fix) |

Prompt wording test (`Jeżeli danej wartości nie ma w dokumencie, podaj null.` instead of `Wartość, której nie ma w dokumencie, to null.`), 60 text appeals:
| Model | Wording | Overall F1 (P / R) | `data` F1 (invented) | `miejscowosc` F1 (invented) |
|---|---|---|---|---|
| Qwen3.5-4B (ARK) | old | 0.921 (0.905 / 0.938) | 0.851 (15) | 0.865 (14) |
| Qwen3.5-4B (ARK) | **new** | 0.920 (**0.931** / 0.910) | **0.905 (9)** | **0.891 (11)** |
| Ministral-3B | old | 0.918 (0.909 / 0.927) | 0.681 (29) | 0.154 |
| Ministral-3B | **new** | 0.911 (0.913 / 0.909) | **0.776 (22)** | 0.151 |

The new wording reduces invented values (higher precision), but the models also return null more often for real values, so F1 stays flat. Kept.

Extensibility check:
- Adding `config/examples/orzeczenie_kio.yaml` (`--doctype ...`) changes only `negatywne/KIO_KIO 2617-21.pdf`, which now becomes an `OrzeczenieKIO` JSON with all 8 fields.
- The other 225 outputs are identical.
- `--check-config` reports broken configs (unknown section, path not in template, unknown derive step) with file and field, and exits with code 3.

### Scans: model reads MinerU OCR text instead of page images (`eval/run_scans_ocr.sh`, 30 scans)
| Model | Scans from images | Scans from OCR text | Cost (30 scans) |
|---|---|---|---|
| Ministral-3B | 0.859 | **0.921** | $0.013 |
| Ministral-8B | 0.881 | **0.920** | $0.019 |
| Ministral-14B | – | **0.977** | $0.026 |
| Mistral-Small-3.2-24B | 0.953 | **0.980** | $0.014 |
| gpt-oss-20b (text-only model) | failed | 0.956 | $0.008 |
| Granite-4.2-8B | – | 0.139 (26/30 calls failed) | $0.10 |

### Full set, scans read as OCR text (`MODELS_OUT=out/models_ocr`)
| Model | Size | F1 | Text | Scans | Appeals | Powers of attorney | Transfers | 1–12 code |
|---|---|---|---|---|---|---|---|---|
| Qwen3-VL-8B | 8.8B | 0.958 | 0.960 | 0.961 | 0.960 | 0.971 | 0.945 | 0.817 |

### OCR engines on the 30 scans (findability)
| Engine | Exact | Incl. near-matches | Speed | Notes |
|---|---|---|---|---|
| MinerU flash | **95.5%** | **98.5%** | 15 s/doc | |
| MinerU basic | 94.7% | 98.1% | 17 s/doc | |
| Tesseract 5.5 `pol` (Docker, 300 dpi, psm 3) | 90.6% | 98.5% | **1.55 s/page, 3.7 s/doc** (4 processes with `OMP_THREAD_LIMIT=1`; without that limit, 9.5 s/page) | misreads every "@" (e-mails 0% exact); IDs fine |
| MinerU standard (VLM **MinerU2.5-Pro-2605-1.2B** GGUF, 1.2 GB, llama.cpp on CPU) | – | 7/7 on the one scan tested (flash also 7/7); perfect diacritics | **155 s for one page** (+ load ≈ 3 min). Far beyond the 18 s/doc budget on CPU | GPU-only in practice |
| Cactus Mirage (51M, `.venv-cactus`) | – | – | ~5 s/page | Unusable: "Gdafisk", "NIP. 9990s642", "REGON. 000ncgug". Digits get corrupted. |

### Hybrids v2 (`python -m eval.hybrid_table`): rules v2 + model fallback; scans via OCR text
`model-first POA` = for powers of attorney the model's answer wins and rules only fill gaps. For appeals and transfers the rules win.

| Setup | Size (points, last published table) | F1 | Text | Scans | Appeals | POA | Transfers |
|---|---|---|---|---|---|---|---|
| rules only | – (20) | 0.950 | 0.964 | 0.855 | 0.975 | 0.868 | 0.964 |
| rules + Ministral-3B | 3.8B (20) | 0.968 | 0.977 | 0.921 | 0.975 | 0.940 | 0.990 |
| rules + Ministral-3B, model-first POA | 3.8B (20) | 0.971 | 0.981 | 0.921 | 0.975 | 0.954 | 0.990 |
| **rules + Qwen3.5-4B (ARK, EU), model-first POA** | 4.66B (20) | **0.982** | 0.988 | 0.942 | 0.976 | 0.990 | 0.993 |
| rules + Bielik-11B v3.0 (ARK, EU), model-first POA | 11.2B (18) | 0.976 | 0.988 | 0.928 | 0.972 | 0.992 | 0.991 |
| rules + Ministral-8B, model-first POA | 8.9B (18) | 0.974 | 0.982 | 0.920 | 0.976 | 0.956 | 0.991 |
| rules + Qwen3-VL-8B, model-first POA | 8.8B (18) | 0.977 | 0.982 | 0.961 | 0.978 | 0.972 | 0.990 |
| rules + Qwen3.5-9B (reasoning off), model-first POA | 9.7B (18) | 0.984 | 0.986 | 0.974 | 0.980 | 0.992 | 0.988 |
| **rules + Ministral-14B, model-first POA** | 13.9B (18) | **0.987** | 0.988 | 0.977 | 0.985 | 0.991 | 0.990 |
| rules + gpt-oss-20B, model-first POA | 20.9B (12) | 0.980 | 0.984 | 0.956 | 0.985 | 0.960 | 0.990 |
| Mistral-Small-24B alone | 24B (12) | 0.981 | 0.981 | 0.980 | 0.979 | 0.995 | 0.968 |
| rules + Mistral-Small-24B, model-first POA | 24B (12) | 0.987 | 0.988 | 0.980 | 0.983 | 0.995 | 0.993 |
| Ministral-14B alone | 13.9B (18) | 0.953 | 0.950 | 0.977 | 0.962 | 0.977 | 0.889 |
| Qwen3.5-9B alone (reasoning off) | 9.7B (18) | 0.958 | 0.955 | 0.974 | 0.955 | 0.992 | 0.919 |
| Qwen3.5-4B alone (ARK, `reasoning_effort: none`) | 4.66B (20) | 0.944 | 0.944 | 0.942 | 0.922 | 0.990 | 0.957 |
| Bielik-11B v3.0 alone (ARK) | 11.2B (18) | 0.937 | 0.942 | 0.928 | 0.914 | 0.991 | 0.961 |

Local Bielik-4.5B Q8 (llama.cpp in Docker, 4 threads): case 01 F1 0.889, ~5.4 min per document. Too slow.

Rejected:
- **Granite-4.2-8B:** 26/30 calls failed on scans.
- **Schematron v2 small / turbo:** F1 0.564 / 0.429. It's built for HTML → JSON against a JSON schema.
- **LFM-3-1.7B:** 0.403, 73 failed calls.
- **Gemma-3-4B:** 0.754, 1–12 code 0.15.
- **Ling-3.0-tiny:** returns only reasoning, no answer.
- **Qwen3.6-35B:** 51 failures (reasoning) and 36B in size.

### Decision models (`python -m eval.decision_bench`): classification (226 docs, first 3000 chars) and 1–12 code (70 appeals incl. scans, action passage)
All via OpenRouter (not EU-hosted; for comparison only). Keyword rules: classification 226/226, 1–12 code 70/70.

| Model | Size | Classification | 1–12 code | Cost (both tasks) |
|---|---|---|---|---|
| baseten/joey-1 | 2B | 0.987 | 0.857 | $0.003 |
| perplexity/pplx-decider-flash-v1.2 | 4B | **1.000** | 0.857 | $0.003 |
| perplexity/pplx-decider-flash-v1.3 (free) | ? | 0.991 | 0.971 | $0 |
| togethercomputer/tev1-4b-experimental | 4B | **1.000** | 0.986 | $0.011 |
| jaredpalmer/kev-4b | 4B | **1.000** | 0.971 | $0.010 |
| cloudflare/clef-flash | 9B | 0.987 | 0.986 | $0.007 |
| nace-ai/drex-v1.5 | 9B | 0.996 | 0.986 | $0.009 |
| baseten/elephant-1 | 9B | 0.991 | **1.000** | $0.009 |
| baseten/groundhog-1 | 26B-A4B | **1.000** | **1.000** | $0.009 |
| baseten/mammoth-1 / bison-1 | 27B / 31B | **1.000** | **1.000** | $0.025 |
| cloudflare/clef-omni / clef | 30B-A3B / 27B | 0.978 / 0.991 | 0.957 / 0.986 | $0.05 / $0.06 |
| nace-ai/drex-v2.0 / drex-max | 35B-A3B / 27B | **1.000** | **1.000** | $0.015 |
| perplexity/pplx-decider-v1.2 / v1.3 | 27B | **1.000** | **1.000** | $0.005 |
| liquid/d1.2, d1.2-ultra, microsoft-decision-1.1, morph/reflex, typesafe/jev-1.14, respan/span-02 | not published | **1.000** | **1.000** | $0.002–0.015 |
| upstage/solar-decide-flash | ? | 0.996 | 0.871 | $0.019 |
| scaledown/classify-1 | ? | 0.996 | 0.957 | $0.014 |

**Local basal-2.0-mini** (~2B, on Bielik-1.5B, `basal-serve --mode eager --device cpu --dtype float32`; bfloat16 is about 14× slower on this CPU):
- 1–12 code: 0.929 (65/70), p50 8.3 s per question.
- Classification (1500 chars): 0.969 (219/226), p50 13.6 s per document.
- It classifies EU tender notices (TED) as appeals.
- Worse than the keyword rules and too slow to run on every document. At most a fallback for single questions.

Latency p50 0.2–0.8 s per question (OpenRouter). Not testable here: **Bielik-One** (needs an NVIDIA GPU or Apple Silicon).

ARK Bielik-11B v3.1: alone 0.902 (8 failures from the parallel-request limit), rules + v3.1 model-first POA 0.970. Worse than v3.0.

### Non-LLM NER (`eval/ner_bench.py`, GLiNER multi v2.1, ~0.2B, local CPU), 70 powers of attorney
| Task | P | R | F1 |
|---|---|---|---|
| Any person named (attorneys, signatories, natural-person principals) | 0.982 | 1.000 | **0.991** |
| Role "pełnomocnik" (zero-shot label) | 0.459 | 0.530 | 0.492 |
| Role "osoba podpisująca w imieniu mocodawcy" | 0.367 | 0.468 | 0.411 |

- Speed: 4.8 s per document on the i7 laptop.
- It finds names but can't assign roles, which depend on sentence context. Not adopted: our regexes already find the names, and the roles come from rules or the LLM.

Section order: the 70 appeals use 8 different header orders (e.g. KIO-Odwołujący-Zamawiający, Zamawiający-Odwołujący, Odwołujący-Zamawiający). Party fields score 0.99–1.00 in every common order. The few misses are scans where OCR merged lines and the heading isn't found.

### Local LLM on CPU (llama.cpp server in Docker, 4 threads, i7-1165G7)
| Model | Speed | F1 (whole-document naive prompt, cases 01–02) | Per document |
|---|---|---|---|
| Bielik-1.5B-v3.0 Q8 | prompt ~35 tok/s, output 7–10 tok/s | 0.25–0.48 (Ministral-3B: 0.94, rules: 0.95–0.97) | ~62 s (budget: 18 s) |

Notes:
- llama.cpp's grammar-based JSON mode fails for Bielik ("Failed to initialize samplers"). Set `NAIVE_NO_RESPONSE_FORMAT=1`.
- Local whole-document extraction doesn't fit the time budget on this CPU. Only very short, narrow prompts could.

### Naive chat pipeline by model (`eval/run_models.sh`, with format rules applied); still running
| Model | Size | F1 | Text | Scans | 1–12 code | Cost |
|---|---|---|---|---|---|---|
| mistral-small-3.2 | 24B | 0.977 | 0.981 | 0.953 | 0.889 | $0.10 |
| gemma-4-26b-a4b | 26B (4B active) | 0.942 | 0.959 | 0.817 | 0.889 | $0.10 |
| ministral-3b-2512 | 3.8B | 0.916 | 0.926 | 0.859 | 0.807 | $0.09 |
| ministral-8b-2512 | 8.9B | 0.901 | 0.904 | 0.881 | 0.880 | $0.13 |

### Hybrid simulation, offline (`python -m eval.merge`): rules first, model as fallback
These merge the saved outputs of the rules pipeline (v1) and of the naive full-document model runs.
- `fill` = the model fills fields the rules left empty.
- `doc` = the model's whole output is used for scans and for documents the rules classified as unrelated.

| Setup | Models (avg size) | Points | F1 | Text | Scans |
|---|---|---|---|---|---|
| rules only | none | 20 | 0.924 | 0.964 | 0.564 |
| rules + Ministral-3B (fill+doc) | 3.8B | 20 | 0.961 | 0.977 | 0.859 |
| rules + Ministral-8B (fill+doc) | 8.9B | 18 | 0.963 | 0.974 | 0.881 |
| rules + Ministral-3B (text) + Mistral-Small-24B (scans) | (3.8+24)/2 = 13.9B | 18 | **0.975** | 0.978 | 0.953 |
| rules + gemma-4-26b-a4b (fill+doc) | 26B | 12 | 0.963 | 0.983 | 0.817 |
| rules + Mistral-Small-24B (fill+doc) | 24B | 12 | 0.974 | 0.977 | 0.953 |
| Mistral-Small-24B alone (+ format rules) | 24B | 12 | 0.977 | 0.981 | 0.953 |
| rules + Clairic + d1.2 (fill+doc), not EU-hostable | 27B + ? | ≤12 | 0.977 | 0.981 | 0.954 |
| rules + Gemma-3-4B (fill) | 4.3B | 20 | 0.920 | 0.957 | 0.596 |
| gpt-oss-20b / nemotron-31b (text-only, so scans fail) | 21B / 32B | 12 | 0.939 / 0.936 | 0.980 / 0.979 | 0.564 |

### Grounding filter, offline (`python -m eval.ground`): null any model value not found in the text
| Model | Before | After grounding | Values dropped |
|---|---|---|---|
| Mistral-Small-24B | 0.977 | 0.977 | 17 |
| Ministral-3B | 0.916 | 0.918 | 95 |
| Ministral-8B | 0.901 | 0.900 | 47 |
| Gemma-3-4B | 0.754 | 0.769 | 453 |

Conclusion: the models' errors are almost never invented strings. They pick the **wrong span**: a date from the body used as the header date, a truncated account number, the wrong party. "Appears somewhere in the text" grounding doesn't catch these. Position-aware constraints (rules and sections) do.

## Readers: findability (`python -m eval.findability`)
"Findability" is the share of gold values that can be found in the reader's text. It is an upper bound on extraction recall.

| Reader | Text PDFs | Scans | Speed |
|---|---|---|---|
| pypdfium2 (text layer) | 100% (1780/1780) | no text | ~instant |
| MinerU flash, `ocr_mode=txt` | 99.9% (one transfer title split differently) | no text | ~1–2 s/doc |
| MinerU flash, `ocr_mode=auto` (OCR) | – | **95.5% exact / 98.5% incl. near-matches**; all IDs 100% | avg 15.1 s/doc, max 48 s (i7-1165G7) |
| MinerU basic, `ocr_mode=auto` (OCR) | – | 94.7% / 98.1%; all IDs 100%; worse on transfer titles | avg 17.0 s/doc, max 60 s |

Notes on MinerU:
- Its Markdown output drops blocks typed `header`, which includes the "Miasto, dnia …" line. Use the middle JSON, which keeps them with bboxes.
- OCR errors seen: "Sp. z 0.0." for "Sp. z o.o.", "Odwohującego" for "Odwołującego", "ODWOLANI E" for "ODWOŁANIE". Digits were correct.

## czynnosc_powod_id (`python eval/powod_bench.py`), 60 text appeals
The key step: `extract/focus.py` cuts out the passage that states the challenged action ("wnoszę o: Zamawiający w dniu … <action>." or "polegających na: 1) …"). Only that passage is classified.

| Method | Size | Passage | Full text (12k chars) |
|---|---|---|---|
| keyword rules (`POWOD_RULES`) | no model | **1.000** | – |
| baseten/joey-1 | 2B | 0.900 | 0.800 |
| perplexity/pplx-decider-flash-v1.2 | 4B | 0.850 | – |
| jaredpalmer/kev-4b | 4B | 0.983 | 0.717 |
| togethercomputer/tev1-4b-experimental | 4B | 0.983 | – |
| cloudflare/clef-flash, nace-ai/drex-v1.5 | 9B | 0.983 | – |
| baseten/elephant-1 | 9B | **1.000** | 0.783 |
| baseten/groundhog-1 | 26B-A4B | 1.000 | 0.850 |
| liquid/d1.2, d1.2-ultra, pplx-decider-v1.2, span-02, microsoft-decision-1.1, morph/reflex, jev-1.14 | size not published | 1.000 | d1.2: 0.917 |
| clef-omni, clef (27B) | 30B-A3B / 27B | 0.967 | – |
| scaledown/classify-1, upstage/solar-decide-flash | ? | 0.950 / 0.817 | – |

Cost: under $0.01 per model for all 60 appeals.
Conclusion: **focusing the input matters more than model size.** The keyword rules may be overfitted to the synthetic wording, so a 4–9B decision model on the passage is a robust fallback.
