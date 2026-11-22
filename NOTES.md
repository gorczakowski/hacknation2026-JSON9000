# UZP – Odwołania2json: research notes

Compiled 2026-11-21 from the task PDFs in this folder, the official repo
https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026, and the MinerU and Clairic docs.
**Official organizer answers live in [FAQ.md](FAQ.md) and override these notes where they disagree.**

## The task in one line
Filled documents from a KIO appeal case (PDF) → classify each document → extract business data → one JSON per document.
These are not forms. They are free-text letters and bank confirmations, so values sit inside paragraphs.

## Document types and expected output (`oczekiwane-json/` in the repo)
- **odwolanie** (appeal, ~5 pages, key info usually on pages 1–3). Root `OdwolanieKIO`:
  - `data` (ISO date), `czynnosc_dzien`, `czynnosc_powod_id` (int 1–12, taken from the **first** charge only), `czynnosc_opis` (always the dictionary label for the ID), `miejscowosc`
  - `Odwolujacy[]`: krs, nazwa, nip, regon, `przez-pelnomocnika` (1/0, hyphenated key), `pelnomocnik {imie_nazwisko, nazwa, mail}`
  - `Zamawiajacy[]`: krs, nazwa, nip, regon, `prowadzacy_postepowanie` (null/1/0)
  - `ZamowieniePubliczne`: numer_bzp, numer_postepowania, numer_ted (NNNNNN-YYYY), tytul
  - The 1–12 enum: 1 odrzucenie oferty, 2 wybór oferty, 3 wykluczenie, 4 publikacja SWZ, 5 modyfikacja SWZ,
    6 odpowiedzi/wyjaśnienia SWZ, 7 zaniechanie odrzucenia, 8 zaniechanie wykluczenia, 9 unieważnienie,
    10 zaniechanie unieważnienia, 11 OPZ, 12 projektowane postanowienia umowy (see `czynnosc_powod_id_dict.md`)
- **pelnomocnictwo** (power of attorney, 1 page): `mocodawca[] {data {imie_nazwisko, krs, nazwa, nip, typ: osoba_prawna|osoba_fizyczna}, podpisujacy[] {imie_nazwisko}}`, `pelnomocnicy[] {imie_nazwisko, pesel}`
- **przelew** (transfer confirmation, 1 page; the input file is `potwierdzenie_przelewu.pdf`, being renamed to `przelew.pdf`): `przelew_bankowy {data_operacji, kwota (float), na_rachunek, numer_transakcji, tytul, z_rachunku}`
- **Unrelated documents** → a JSON containing exactly `{}`, named after the PDF. 12 examples are in `dane/negatywne/` (KIO rulings, EU/BZP tender notices, ministry letters, a JPK brochure). Some example folders contain them too.
- The spec files contain `//` comments and at least one missing comma, so they are not valid JSON as published.
- Normalization seen in the labels: names drop titles ("r. pr. Cezary Walczak" → "Cezary Walczak"), dates in ISO format, amounts as floats, account numbers digits only.

## Verified facts about the training data (cloned 2026-11-21)
- Local copy: `./hacknation-2026/` (a git clone; run `git pull` there to get organizer updates). Snapshot at commit `e8b7a67`.
- 60 cases (`dane/przyklad_01..60`), each with odwolanie.pdf, pelnomocnictwo.pdf, potwierdzenie_przelewu.pdf, and 3 JSONs. 198 PDFs in total.
- **All 180 case PDFs have a clean text layer** (synthetic, digitally generated). No OCR is needed for training.
- The hidden test set is synthetic, generated from real cases (FAQ). Expect scans and varied wording anyway: `dane/skany/` (added 11-21) has 30 image-only scans. **OCR is required**, and don't overfit regexes to the synthetic set.
- File names may differ in the test set. **Classify by content, not by file name** (the README says so explicitly).

## Run interface
- Python 3.13+, `requirements.txt`, launched via CLI or `start.bat`.
- `./input/<case>/*.pdf` → `./output/<case>/*.json`, with each JSON named after its input PDF. Template: `szablon-aplikacja/`.
- Test machine: office laptop, 16 GB RAM, Intel i7, **no GPU**.
- An LLM is allowed only via API, with the key supplied by us. The API must be run by a company with servers in the **EU/EEA**. Open-source / open-weights models only.
- Prize payout requires Docker Compose, architecture docs, and docs on how to configure extraction and add a new document class.

## Scoring
- Stage 1 (preselection): **F1 only** on the hidden set. The top 3 teams go to the final.
- Final: F1 50% + flexibility. The sources disagree on the split:
  - rules PDF: 50 / 50
  - task PDF: 50 / 30 extensibility / 20 architecture
  - README: 1–20 pts LLM size (**smaller is better**), 1–20 adding new document types and data classes via config, 1–10 extras such as a GUI
- Bonus for: new document classes configurable without code changes; transparent extraction (show where each value came from).
- The F1 method is not defined (per field or per document? how values are normalized?). **Ask a mentor.**

## Tooling findings
### MinerU (PDF/scan → Markdown/JSON with layout and bounding boxes)
- Latest is 4.2.3 (16 Nov 2026). The 4.x line renamed everything, so ignore 3.x tutorials (`-b pipeline`, `hybrid-*`, `mineru-api`).
- Tiers: flash (native text/Office, no models), basic (small ONNX models: OCR, layout, tables; **runs on CPU**), standard/advanced (needs a VLM, ~8 GB VRAM as a starting point).
- Python 3.11–3.14. License changed from AGPL to the "MinerU Open Source License" (Apache 2.0 + attribution + limits on very large companies) in 3.1.0.
- Polish OCR is **not documented**. Test diacritics (ą ę ł ż ź ś ć ń ó) on a scan.
- For UZP: **use direct text extraction (e.g. pypdfium2) for PDFs with a text layer; use MinerU only as the OCR fallback** for pages without text. Parse only the first ~3 pages of the appeal.
### Clairic / Clairic-flash (Cloudflare extraction model, open weights released 2026-11-18)
- Schema of typed fields in (text, number, date, enum, boolean, list of objects one level deep) → typed values out, each with `confidence`, `p_null` and a character `span`.
- Clairic is 27B (on Qwen3.8-27B); Clairic-flash is 9B (on Qwen3.5-9B). Apache 2.0, on HuggingFace as `Cloudflare/clairic`, `Cloudflare/clairic-flash`.
- Limits: ~65k token context, ≤4 images or PDF pages per request. Benchmarks are self-reported. **Polish support is unknown.**
- Strong conceptual fit for UZP:
  - each document class = a field config (the configurability bonus)
  - `span` = transparency
  - `p_null` / confidence thresholds = precision control and handling of negative documents
  - `enum` = czynnosc_powod_id
- Hosting problem: Workers AI and OpenRouter are US companies, and no EU hosting was found. Options:
  - self-host on an EU GPU cloud (OVHcloud, Scaleway)
  - run a quantized version (the typed-field API is custom Python; unverified whether it survives in llama.cpp)
  - use a general small open model with JSON-constrained output instead
- 9B in BF16 is ~20 GB of weights (my estimate), which won't fit the 16 GB test laptop.

## Suggested pipeline
1. Read the PDF: text layer if present, otherwise MinerU OCR (first ~3 pages).
2. Classify: odwolanie / pelnomocnictwo / przelew / none (empty JSON).
3. Extract with regex for identifiers (NIP, REGON, KRS, PESEL, IBAN, amounts, dates, BZP/TED numbers) and a small LLM (or Clairic) for names, roles and the 1–12 reason, all driven by a per-class config file.
4. Validate (NIP/REGON/PESEL/IBAN check digits), normalize, and keep evidence (page and span) for each value.
5. Build an F1 scorer against the 60 labeled cases on day 1 and iterate against it.

## Open questions for mentors
- Exact F1 definition and normalization rules.
- Does a hosted open-weights model from an EU provider satisfy "open-source LLM via API"?
- How the final-stage criteria are actually split.
- Docker Compose vs `requirements.txt`: what is expected at submission?

## Comparison with the other challenge (COI, `../coi_cyfryzacja_formularzy`)
COI = blank form (PDF/DOC/ODS/XLSX/scan) → configuration of a digital e-service form, mostly assembled from standard blocks in their design guide.
UZP is easier to start (clean text, flat schema, objective metric) but harder to stand out in (pure F1 race on unseen real data).
COI is more engineering (REST API, hardened Docker, 7 formats, local LLM only, video and slides), scored by a jury. Prizes: UZP 20,000 zł gross, COI 25,000 zł.
