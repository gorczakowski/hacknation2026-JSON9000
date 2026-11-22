# JSON9000 — Odwołania2JSON

Turns the documents of a KIO appeal case (PDF, text or scanned) into JSON:
**odwołanie** (appeal), **pełnomocnictwo** (power of attorney), **potwierdzenie przelewu** (bank transfer
confirmation). Any other document gets `{}`. Built for the UZP challenge at HackNation 2026.

**Result on the 226 training PDFs** (60 cases + 30 scans + 16 unrelated), in a 2-CPU container like the evaluation
VM: **F1 0.988**, all 226 documents classified correctly, 30 minutes in total (details in [EXPERIMENTS.md](EXPERIMENTS.md)).

## How it works

```
PDF ─► read ───────────► classify ─────────────► extract ──────────────► combine ─────────► derive + normalize ─► JSON
       text layer         weighted phrase rules   rules from the type's    rules + model        1-12 code, flags,
       (pypdfium2),       per type (YAML);        YAML: sections, labels,  (fill gaps / model   formats (IBAN, BZP,
       OCR for pages      model decides only      typed values, lists      leads where rules    TED, dates), title
       without text       when the rules are                               are weak)            stripping
       (MinerU/Tesseract) not sure
```

- **Rules first.** Every document type is described in one YAML file
  ([config/doctypes/](config/doctypes/)): how to recognise it, where its parts are, and how to read each field.
  Rules are exact, explainable, fast (226 PDFs in ~5 s without OCR) and never invent values.
- **A small model where rules are weak.** An open-weights LLM (**Qwen3.5-4B**, 4.66B parameters, Apache-2.0) is
  called through **ARK Labs** (Polish company, inference on EU servers). It is used for:
  - **classification**, only when the rules are not sure (score close to the threshold or two types close)
  - **empty fields**: the rules' answer wins, the model fills what the rules missed
  - **powers of attorney and scans**: the model's answer wins, the rules fill gaps (rules are weaker there)
  - **the 1–12 code**, only when no keyword rule matches the challenged-action sentence
  Model answers are checked before use (valid IBAN, real date, right number of digits) and dropped if implausible.
- **Local OCR.** MinerU (flash tier, small ONNX models, ~270 MB) runs locally on CPU. Tesseract (Polish) is the
  faster fallback. There is no OCR API.
- **Evidence.** For every document, `evidence/<name>.json` records the type scores, who decided (rules or model),
  and for each value the method and the surrounding text it came from. Check-digit results for NIP/REGON/PESEL/IBAN
  are reported there too (never used to change values).
- **Safety nets for the time limit.** After 25 minutes the remaining scans use Tesseract, and after 40 minutes the
  remaining documents are processed with rules only, so every PDF still gets its output. Runs that finish earlier
  are not affected.

## Running it

**On the evaluation server** (see [RUN.md](RUN.md)):
```bash
cd /opt/solution && docker compose run --rm -T --name JSON9000 json9000
```

**Locally with Docker** (input in `./input`, results in `./output`):
```bash
cp .env.example .env            # put ARK_API_KEY=... in it (optional: without it the program runs rules-only)
docker compose build
docker compose run --rm -T --name JSON9000 json9000
```

**Locally with Python 3.13+** (without Docker; OCR needs MinerU and/or `tesseract` installed):
```bash
pip install -r requirements.txt
python -m odwolania2json --input input --output output
```

Useful options: `--no-llm` (rules only), `--ocr-engine tesseract`, `--doctype config/examples/orzeczenie_kio.yaml`
(add a document type), `--check-config` (validate configuration and exit), `--strict` (non-zero exit if a scan could
not be read), `-v` (log details).

## Configuration

| File | What it controls |
|---|---|
| [config/pipeline.yaml](config/pipeline.yaml) | OCR engine and fallback, model provider/model/key name, when the model is asked (`hybrid`), time safety nets, parallelism |
| [config/doctypes/*.yaml](config/doctypes/) | one file per document type, loaded by default |
| [config/templates/*.json](config/templates/) | the organizers' output templates (with field comments, also used as instructions for the model) |
| [config/examples/](config/examples/) | extra document types, not loaded by default (enable with `--doctype`) |
| `.env` | API keys (never committed) |

Switching the model is a config change, e.g. Ministral-14B on Mistral's EU endpoint or Qwen3.5-9B on IONOS
(commented examples in `pipeline.yaml`).

## Adding a new document type (no code changes)

See **[docs/ADDING_A_DOCTYPE.md](docs/ADDING_A_DOCTYPE.md)**. In short: write one YAML file with the output shape,
recognition phrases with points and a threshold, optional sections, and extraction methods per field. Then run
`--check-config`, calibrate the threshold on sample documents (`python -m odwolania2json.calibrate`), and add it with
`--doctype`. The worked example [config/examples/orzeczenie_kio.yaml](config/examples/orzeczenie_kio.yaml) (KIO
rulings) was added this way; enabling it changes only the ruling's output and leaves all other outputs identical.

## Code map

| Module | Role |
|---|---|
| [odwolania2json/__main__.py](odwolania2json/__main__.py) | command line, parallel processing, time safety net, summary |
| [reading.py](odwolania2json/reading.py) | text layer per page, OCR for pages without text (MinerU / Tesseract / fallback) |
| [pipeline.py](odwolania2json/pipeline.py) | config loading, rule classification, rule extraction, lists |
| [extract.py](odwolania2json/extract.py) | extraction methods (label → typed value, regex, sections, names, place/date, …) |
| [values.py](odwolania2json/values.py) | typed value parsers: dates (Polish months), amounts, IBAN/NRB, BZP, TED, IDs |
| [hybrid.py](odwolania2json/hybrid.py) | when the model is asked, combining rules and model answers, sanity checks |
| [llm.py](odwolania2json/llm.py) | OpenAI-compatible client with retries and a parallel-request limit |
| [derive.py](odwolania2json/derive.py) | named post-processing steps used by configs (1–12 code, flags, normalization, signatories) |
| [validate.py](odwolania2json/validate.py) | configuration checks at startup |
| [calibrate.py](odwolania2json/calibrate.py) | threshold helper for document types |
| [validators.py](odwolania2json/validators.py) | NIP / REGON / PESEL / IBAN check digits (reported, not enforced) |

Evaluation and research tooling is in [eval/](eval/) (F1 scorer following the organizers' rules, OCR "findability",
model and decision-model benchmarks) and [naive/](naive/) (pure-model baselines). Notes: [NOTES.md](NOTES.md)
(task research), [FAQ.md](FAQ.md) (organizers' answers), [MODELS.md](MODELS.md) (models and EU hosts),
[EXPERIMENTS.md](EXPERIMENTS.md) (all measurements).
