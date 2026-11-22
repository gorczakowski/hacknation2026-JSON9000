# UZP Odwołania2json: FAQ and announcements

Official answers from the organizers (Discord, the UZP team) plus facts we checked in the repo.
**Answers here override NOTES.md where they disagree.**
Dates are 2026. Sources: `[D]` = Discord message, `[R]` = organizer repo, `[V]` = verified by us.
Add newer entries at the top of each section.

## Scoring / F1

**How is F1 counted?** `[D]` MateuszSz, 11-21 11:26
- **TP**: the value is in our JSON and matches the ground truth.
- **TN**: the value is absent from both our JSON and the ground truth.
- **FP**: the value is only in our JSON, **or** it is in both but wrong.
- **FN**: the value is in the ground truth but not in our JSON.
- So a wrong value costs more than a missing one (it is an FP, and the GT value goes unmatched). When unsure, `null` is safer than a guess.
- No official scoring script has been published yet (people asked for one at 11:17).

**Which fields are fuzzy-matched?** `[D]` MateuszSz, 11-21 11:26 and 13:26
- These are "long strings" matched at **≥80% similarity**: the attorney's company name (`pelnomocnik.nazwa`), the transfer title (`przelew_bankowy.tytul`), the Odwołujący/Zamawiający name (`nazwa`), and the public procurement title (`ZamowieniePubliczne.tytul`).
- Everything else is presumably exact match: KRS, NIP, REGON, PESEL, dates, amounts, IDs, e-mail, person names, `czynnosc_opis`.
- The similarity measure behind the 80% was not stated. Asked, not answered.

**How are lists matched (several Odwołujący, pełnomocnicy, …)?** `[D]` MateuszSz, 11-21 13:26
- By content, not by order.

**⚠ Model-size scoring is being revised (11-21 evening):**
- The README now says: "W wyniku dyskusji z uczestnikami i zgłoszeń o potencjalnych sposobach manipulacji, pracujemy nad nowymi wytycznymi. stay tuned." (After discussion with participants and reports of possible ways to game it, they are working on new guidelines.)
- The table below may change. The averaging of several models is the most likely part to go.

**How are model-size points counted? (official, under revision)** `[R]` README a48d25b, 11-21 18:46 `[D]` Dominik Olczak 18:26
- Points depend on the **average parameter count of all LLMs used**. For MoE models, the **total** parameter count is used, not the active one.
- This replaces the in-person mentor answer below.

  | Average model size | Points |
  |---|---|
  | ≤ 8B | 20 |
  | >8B – 14B | 18 |
  | >14B – 32B | 12 |
  | >32B – 70B | 8 |
  | >70B – 150B | 4 |
  | >150B | 0 |

- **Several models:** the plain average counts. README example: 3B + 32B → 17.5B → 12 pts.
- **Models used only locally**, if they run on the test machine (16 GB RAM, i7, no GPU) and process the documents in the expected time, get **20 points**. So local models are allowed after all, which contradicts the 12:47 answer ("we don't install models locally").
- **OCR:** local or via API. **OCR via API counts like an LLM** and goes into the average. Local OCR (e.g. MinerU on CPU) presumably doesn't count.
- **Maximum processing time** `[R]` README "Maksymalny czas przetwarzania", 11-21 evening:
  - The test is **150 PDFs, with at most 45 minutes for the whole set**, which averages about 18 s per PDF.
  - Local MinerU OCR (about 15 s per scan on our machine) fits.
  - A local LLM on CPU only fits for short, narrow prompts.
- **We get one submission,** scored for both F1 (preselection) and the final (user's understanding, 11-21).
- A rules-only solution (no LLM) gets the maximum points (`[D]` 13:52).

**Earlier mentor answer, superseded:** `[mentors, in person]` 11-21
- Up to 1B was full points, then −1 point per extra 1B.

**Does a solution without an LLM get the maximum points for model size?** `[D]` MateuszSz, 11-21 13:52
- Yes.

**What are the evaluation weights?** `[D]` announcement, 11-21 9:38
- Preselection: 100% F1.
- Final: 50% F1 + 50% flexibility and extensibility.
- This matches the rules PDF. The 50/30/20 split in the task PDF seems outdated. The README's points for model size, new doc types and GUI presumably sit inside the 50% flexibility part.

## Output format

**⚠ Account numbers keep the country prefix (changes our normalization)** `[D]` MateuszSz 11-21 21:17, _rk 23:00
- The value must be "the number contained in the document".
- The training JSONs without "PL" are an **error, which won't be in the evaluation set**.
- Format: **no whitespace, with "PL" (or another country code) if the document has it.** `PL05 4706 8028 …` → `PL05470680287581539721455024`. No prefix in the document → digits only.
- Our training gold lacks the prefix, so the scorer must strip it for local evaluation. The submission must keep it.

**Company names: exactly as written in the document** `[D]` MateuszSz 11-21 21:17
- For a sole trader in a power of attorney, `nazwa` is the company name as written in the document. Sometimes that differs from the CEIDG business registry.

**Output file naming (confirmed)** `[D]` _rk 11-21 22:19
- `potwierdzenie_przelewu.pdf` → `potwierdzenie_przelewu.json`, i.e. named after the input PDF, not the training-example name `przelew.json`.
- An out-of-class document → a JSON containing only `{}`, with no extra fields (MateuszSz 22:32).

**Can we add extra fields (e.g. a source quote) to the JSON?** `[D]` Dominik Olczak, 11-21 12:47
- No. The JSON must follow the format strictly. Keep evidence and transparency data outside the scored JSON, e.g. in a sidecar file or the GUI.

**What do we output for a document that isn't one of the 3 types?** `[D]` MateuszSz, 11-21 13:27 and 13:50
- A JSON file with the original file's name, containing exactly `{}` (not a 0-byte file).
- This corrects the 12:47 answer ("don't return a JSON").
- The classifier should still report internally that no type matched (e.g. log or print it).

**How are names normalized?** `[D]` MateuszSz, 11-21 15:55 `[R]` commits 6c6dc79 and 96aea5a
- Person names go in the **nominative** case: "Mateusz Szymański", not "Mateusza Szymańskiego". Polish documents often use the genitive or dative ("udzielam pełnomocnictwa Janowi Kowalskiemu"), so a declension step is needed.
- The spec comments for `pelnomocnik.imie_nazwisko` and `mocodawca.imie_nazwisko` now say "w mianowniku" (in the nominative).

**What does `czynnosc_powod_id` 12 mean?** `[D]` MateuszSz, 11-21 13:13 `[R]` `oczekiwane-json/czynnosc_powod_id_dict.md`
- 12 = "Projektowane postanowienia umowy" (proposed contract provisions).
- The training set had only 1–11 at first. One example of 12 now exists (in `dane/skany`).

**Is `czynnosc_opis` free text?** `[V]` all 70 labeled appeals
- No. In every label, `czynnosc_opis` is exactly the dictionary label for `czynnosc_powod_id`.
- So it is a lookup from the ID, not an extraction. Use the dictionary strings verbatim, with `/` and not "i".
- The organizer fixed this in cases 19, 23, 25, 44 and 55 (`[D]` 11-21 13:43 and 14:17).

## Data

**60 cases or 39? Where is `ground_truth.yaml`?** `[D]` Dominik Olczak, 11-21 12:47
- 60 is correct. The presentation's 39 is outdated, because more cases were added on 11-21.
- `ground_truth.yaml` was removed because it was just the merged JSON files.

**Is the test set real, or are there scans?** `[D]` Dominik Olczak, 11-21 12:47 and 15:49
- The test set is **synthetic documents generated from real cases**. It is not real documents.
- At 12:47 they said there were no scans without a text layer in the examples. At 15:49 they added `dane/skany/` with scanned documents.
- Assume the test set may contain scans.

**What's in `dane/skany/`?** `[V]` after `git pull` on 11-21
- 30 documents: `odwolania/`, `pelnomocnictwa/` and `potwierdzenia/`, 10 of each (`0001`–`0010`), each a PDF with its JSON.
- **The PDFs are image-only (no fonts and no text layer), so OCR is required.**
- The appeals are 2.4–3.6 MB.

**Do identifiers pass checksums?** `[V]` all labels, 11-21
- Often not. These values in the labels fail their check digits:
  - NIP: 74 of 218
  - REGON: 38 of 132
  - account numbers (IBAN mod 97): about 55%
- The data is synthetic, so **checksums can't reject or "repair" values**. At most they are a weak signal.

**What types do the labels use?** `[V]`
- `pesel` is a **string** in the labels (`"85120133547"`), even though the spec file shows a number. It is `null` in 110 of 115 cases.
- `kwota` is a float (`200.0`). We write it with two decimals (`200.00`), as `oczekiwane-json/przelew.json` shows (`10000.00`); it is the same number for any JSON reader (`values.dump_output`).

**Are file names stable?** `[R]` commits d1d980c and 84cc976 on 11-21 `[V]`
- No. The organizer has started renaming `potwierdzenie_przelewu.pdf` → `przelew.pdf`. So far only `przyklad_01` and `przyklad_02` are done.
- The scans use numeric names (`0001.pdf`).
- **Classify by content.** The output JSON name follows the input PDF name.

**Are there unrelated documents inside the case folders?** `[V]`
- Yes, in 4 case folders, with no JSON for them. The expected output is `{}`:
  - `2025-OJS107-00364525-pl-ts.pdf`
  - `2025_BZP 00143067_01.pdf`
  - `2025_S 112-383751.pdf`
  - `prowadzenie_postepowania_odwolawczego.pdf`

## ⚠⚠ Evaluation on our own Azure VM (`hacknation-2026/VM.md`, 11-22 00:38): this replaces the laptop
- **Request a VM via the form:** https://forms.cloud.microsoft/e/HRFJjEcqMF (one per team; team names are checked against registrations). Login, password and IP come from the organizers.
- **Team access is cut off on 22.11.2026 at 11:00.** The solution is evaluated as left. Background services and containers keep running.
- **Machine:** Ubuntu 24.04, **2 vCPU / 16 GB RAM**, 64 GB disk, **no GPU**. Open ports 22/80/443/8000/8080. Internet access: yes (apt, pip, Docker Hub, GitHub, external APIs).
- **Preinstalled:** Docker + Compose v2 (our user is in the `docker` group), git, **Python 3.12** with venv/pip, build-essential. We have `sudo`, so `apt install` is possible (e.g. tesseract-ocr-pol).
- **How they evaluate:**
  1. Cut our access.
  2. Copy the secret set to **`/opt/solution/input`**.
  3. In **`/opt/solution`**, run **the command from `RUN.md`** (as sudo, non-interactive; it must finish on its own).
  4. Collect **`/opt/solution/output`**.
- **Requirements:**
  - The whole project lives in `/opt/solution`. Don't delete or rename `input/` and `output/` (they may be cleared before evaluation).
  - Background services must survive a reboot (`restart: unless-stopped`), or the command starts them itself.
  - **Download models, images and dependencies in advance.**
  - **API keys go in `/opt/solution/.env`**, and `RUN.md` describes which services are used.
  - Test exactly as they will: put files in input, clear output, run the RUN.md command, also after `sudo reboot`.
- Linux replaces `.bat` (Dominik Olczak 12:53: 70+ teams, automated evaluation).
- **No combined PDFs** (e.g. a power of attorney with a KRS extract attached) in the evaluation set. Dominik Olczak 00:03.

## Evaluation (news from 11-21 evening)
- **Scans are part of the evaluation**, alongside text PDFs: "Planowana jest na obydwu" (planned for both). MateuszSz 22:23.
- **Document count and time limit are no longer fixed:** "Na ten moment nie wiemy jaka dokładnie będzie liczba dokumentów i time limit" (we don't know the exact number of documents or the time limit yet). MateuszSz 22:32. The README still says 150 PDFs / 45 min.
- **The number of teams exceeded their plans.** They're working on a fair evaluation method and hope to announce it "today" (_rk 22:19). They also haven't said whether Tesseract will be installed on the test laptop.
- **Rate limits** and the number of requests don't affect the score, but each solution gets a fixed amount of time and may not finish. MateuszSz 21:13.
- **Flexibility scoring** includes configuring recognition rules, validation and field mapping, not only adding document types. **A new document class must be added via a config file.** MateuszSz 22:32.
- **A small local NER model** (not an LLM, about 0.2B parameters, run locally like OCR) **is allowed.** _rk 22:44.

## LLM / runtime

**Which ways of using an LLM are allowed?** `[D]` MateuszSz, 11-21 11:23, 13:51 and 15:17 `[R]` README 8a0cd62
- The model must be **open-source or open-weights**, reached **via API**, and hosted in the **EU/EEA**. We hand over the API key for evaluation (`[D]` Dominik Olczak 11:28; they promise to use it only for testing).
- **Not allowed:** hosting by a private person, e.g. on our own computer. This was changed at 15:17 and overrides the earlier "all 3 options are fine".
- The host must be a company whose **privacy policy states the scope and location of data processing**.
- In practice: a commercial EU provider serving open-weight models, or our own deployment on a company cloud (e.g. an EU GPU cloud), if that counts. Ask whether self-deploying on OVH or Scaleway counts as "company hosting".

**⚠ README 0b360a9 (11-21 19:36): the test-machine description was removed.**
- "16 GB RAM, Intel i7, no GPU" is gone. It now says: "Jest ok. 30 zespołów zainteresowanych. Musimy zmienić planowany sposób oceny. Stay tuned." (About 30 teams are interested; the evaluation method has to change.)
- The "local-only models get 20 pts" sentence was removed together with the points table.
- The 150 PDFs / 45 min limit stays.
- Our dev machine (i7-1165G7, 4 cores, 48 GB) is a fair proxy for the old spec.

**What machine and time limit are used for evaluation? (older answer)** `[D]` Dominik Olczak, 11-21 12:47 `[R]` README
- An ordinary office laptop: 16 GB RAM, Intel i7, no GPU.
- **There is no time limit.**
- Nothing gets installed locally model-wise, hence the API.
- The machine has internet, so our endpoint must be publicly reachable.

**What are the technical requirements?** `[D]` MateuszSz, 11-21 9:42
- Python 3.13+.
- Dependencies in `requirements.txt` with simple pip installation.
- Open-source components only.
- Runs locally without a GPU.
- Simple launch: a run command with arguments, or a `.bat` file.

## Submission

**How is the solution submitted, and how do we hand over the API key?** `[D]` MateuszSz, 11-21 17:20
- Through a form on the HackNation website.
- The API key goes into any of the form's answer fields ("we won't be picky which one").
- Never commit the key to the repository.

**Can the jury evaluate a private GitHub repo?** `[D]` Dominik Olczak, 11-21 16:23
- Yes. Give access to these GitHub accounts:
  - `DominikOlczak`
  - `MateuSzSz-Sz`
  - `rkrol-dev`

## Still open (ask a mentor)
- Do local non-LLM models (OCR, a small classifier) count toward the LLM average? Presumably not, since the README talks only about LLMs and OCR via API.
- What similarity measure is behind the 80% threshold (Levenshtein ratio? token-based?), and is it applied after normalization (case, whitespace, diacritics)?
- Is `null` in the ground truth vs a missing key in our output counted as TN?
- Is F1 aggregated per field across all documents, or per document and then averaged? Does document classification count separately?
- Does self-deploying an open model on an EU company cloud (OVH/Scaleway GPU) count as allowed hosting?
- Will a scoring script be published?
