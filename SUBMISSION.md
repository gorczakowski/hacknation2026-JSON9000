# Submission checklist

Things that must change between testing and the final submission. Check every item before submitting.

## LLM provider / API
- [ ] **Switch the Mistral endpoint to the EU one: `https://api.eu.mistral.ai`** (10% surcharge).
  - The global `api.mistral.ai` (used for cheaper testing) makes no promise about where data is processed, so it doesn't meet the EU/EEA rule.
  - OpenRouter is for experiments only (US company, routing not EU-guaranteed).
- [ ] Turn off training on our data in the Mistral account. Turn on Zero Data Retention if it's available (pay-as-you-go only).
- [ ] Create a **dedicated API key** for the jury, with a spending limit. Put it in the HackNation submission form, **never in the repo**.
- [ ] Re-check the README for the final model-size scoring rules (being revised as of 11-21).
- [ ] Check that every model used is open-weights and its exact size is documented (README requirement: "opis wykorzystanego LLM", a description of the LLM used).

## Runtime
- [ ] Python 3.13+, `requirements.txt` with simple `pip install`, `start.bat` / CLI.
- [ ] Docker Compose (needed for the prize), with OCR models (MinerU and/or Tesseract `pol`) **downloaded at build time**. The first run must not download several GB.
- [ ] Time guard: 150 PDFs in at most 45 min. Write each output as soon as it's done. Near the deadline, skip OCR and write `{}`.
- [ ] Every input PDF gets an output JSON, named after the PDF. Unrelated documents get `{}`.
- [ ] Evidence/debug files go outside `output/`.

## Repo
- [ ] For a private GitHub repo, give access to `DominikOlczak`, `MateuSzSz-Sz`, `rkrol-dev`.
- [ ] Architecture docs, plus "how to configure extraction / add a new document type" docs.
