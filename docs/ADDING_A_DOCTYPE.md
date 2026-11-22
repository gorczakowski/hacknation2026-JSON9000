# Adding a new document type

A document type is **one YAML file**. No code changes are needed. This guide uses the worked example
[config/examples/orzeczenie_kio.yaml](../config/examples/orzeczenie_kio.yaml) (KIO rulings).

## Workflow

1. **Collect samples:** a few PDFs of the new type, and a folder of documents that are *not* this type.
2. **Copy the example** to `config/examples/<name>.yaml` (or `config/doctypes/` to load it by default).
3. **Describe the output** (`output:`), **how to recognise it** (`classify:`), and **how to read each field** (`fields:` / `lists:`).
4. **Validate:** `python -m odwolania2json --check-config --doctype config/examples/<name>.yaml`.
   Problems are listed with file and field (e.g. an unknown section, a path that is not in the output, a broken regex).
5. **Calibrate the threshold:**
   ```bash
   python -m odwolania2json.calibrate --type <name> --doctype config/examples/<name>.yaml \
       --positives samples/<name> --negatives samples/other
   ```
   It shows how often each phrase rule matches in each group, every document's score, and a suggested threshold
   in the gap between the groups (or a warning when they overlap).
6. **Run** with `--doctype config/examples/<name>.yaml` and check `output/` and `evidence/`, which shows where each
   value came from.

## The file

```yaml
name: orzeczenie_kio                 # unique type name

output:                              # the JSON shape (keys + nesting); values are only examples
  OrzeczenieKIO:
    sygnatura: "KIO 2617/21"
    data: "2021-09-20"
    ...
# or instead:  template: my_type.json   (a separate JSON file, // comments allowed; looked up next to the YAML,
#                                        in its templates/ folder, or in config/templates/)

classify:
  description: "Orzeczenie KIO (wyrok lub postanowienie) z sygnaturą akt KIO."   # for the model, when rules are unsure
  threshold: 6                       # minimum score to count as this type
  sure_margin: 2                     # optional: rules decide alone if score >= threshold + this (default from pipeline.yaml)
  priority: 0                        # optional: breaks exact ties between types
  rules:                             # phrases with points; matched case- and diacritics-insensitively
    - {pattern: '^\s*sygn\.\s*akt\s*:?\s*KIO', weight: 4}
    - {pattern: '^\s*(wyrok|postanowienie)\s*$', weight: 3}
    - {pattern: '^\s*orzeka\s*:?', weight: 2, anywhere: true}   # default: first 4000 chars; anywhere: whole text
    - {pattern: '^\s*odwołujący\s*:', weight: -4}               # negative points for look-alikes

sections:                            # optional named parts of the document
  sentencja:
    start: ['^\s*orzeka\s*:?']       # first match starts the section
    end: ['^\s*stosownie\s+do', '^\s*uzasadnienie']   # also ends at the start of any other section
    max_chars: 3000

fields:                              # output path -> extraction methods, tried in order; first value wins
  OrzeczenieKIO.sygnatura:
    - {regex: 'sygn\.\s*akt\s*:?\s*(?P<v>KIO\s*\d+/\d+)'}
  OrzeczenieKIO.data:
    - {regex: 'z\s+dnia\s+(?P<v>\d{1,2}\s+\w+\s+\d{4})', type: date}
  OrzeczenieKIO.rozstrzygniecie:
    - {regex: '^\s*1\.\s*(?P<v>[^;\n]+)', scope: sentencja}   # search only inside a section
  OrzeczenieKIO.numer_bzp:
    - {find: bzp}

derive: [normalize]                  # named post-processing steps (see below)

llm:
  mode: fill                         # fill | model_first | off  (how rules and the model are combined)
  instructions: ""                   # optional extra instructions for the model
```

## Recognition (`classify`)

- The score is the sum of the weights of the rules that match.
- A type qualifies when its score reaches its `threshold`. Among qualifying types, the highest score wins.
- The rules are **sure** when the winner is at least `sure_margin` above its threshold and at least `sure_margin`
  ahead of every other type. Then no model is called.
- When every type scores 0 or less, the document is unrelated (`{}`).
- Otherwise the model chooses using the types' `description` sentences. If no model is configured, the rules'
  answer is used.

## Extraction methods

| Method | Example | Meaning |
|---|---|---|
| `label` + `type` | `{label: ['kwota\s+operacji', 'kwota'], type: amount}` | the value right after one of the labels (first label that yields a value wins) |
| `regex` | `{regex: 'NIP\s*(?P<v>\d{10})'}` | named group `v` (or group 1); optional `type` to parse it; `dotall: true` |
| `find` | `{find: email}` | first value of a kind anywhere in the scope: `email`, `bzp`, `ted`, `date` |
| `place_date` | `{place_date: date}` / `{place_date: place}` | from a header line like "Gdańsk, dnia 15 lipca 2026 r." (`date_only_before: '<heading regex>'`: also a date-only line at the top or right above that heading) |
| `first_line` | `{first_line: true, skip: '^Konsorcjum'}` | first non-empty line of the scope (e.g. a company name after "Odwołujący:") |
| `names_after` | `{names_after: ['udzielam\s+pełnomocnictwa'], first: true}` | person names after a phrase, titles (r. pr., adw.) stripped |
| `nth` | `{nth: 1, of: account, min_count: 2}` | the n-th account number in the scope (fallback when there are no labels) |
| `const` | `{const: "osoba_prawna"}` | a fixed value |

- Every method can take `scope: <section>`. If that section is not found in the document, the method is skipped
  and the next one is tried.
- `final: true` makes a method authoritative: when it can run (no scope, or its section was found), its answer
  stands even if it found nothing. Later methods are skipped, and in `fill` mode the model does not fill the field.
  Use it when the absence of a value in the right place means "no value", e.g. notice numbers read only from the
  "dotyczy" block, because the body may cite other proceedings. It applies to text-layer PDFs only; on OCR text it
  is ignored, because OCR can separate a label from its value.
- `type` values: `date` (ISO; Polish month names supported), `amount` (float), `account` (IBAN with country
  code, or a 26-digit NRB), `digitsN` (e.g. `digits10`), `email`, `token`, `bzp`, `ted`, `text` (multi-line, up to
  `stop:` labels), `line`.

**Lists** (e.g. several attorneys, consortium members) go under `lists:`:
```yaml
lists:
  Zamawiajacy:                  # output path of the list
    scope: zamawiajacy          # section to read from
    split: '^\s*\d\)\s'         # optional: split the section into items ("1) ...", "2) ...")
    max_items: 3
    fields:                     # paths inside one item
      nazwa: [{first_line: true}]
      nip:   [{label: ['NIP'], type: digits10}]
```
Or one item per name found: `from_names: [...]` with `name_field: imie_nazwisko`.

## Post-processing (`derive`)

Named steps from the built-in library, applied in order:

| Step | What it does |
|---|---|
| `normalize` | IDs to digits; accounts without spaces, keeping the country code; BZP and TED formats; positive amounts; titles stripped from names |
| `powod_and_date` | the 1–12 challenged-action code. Configured in the same YAML under `powod:` (passage patterns, keyword rules, labels); see [odwolanie.yaml](../config/doctypes/odwolanie.yaml) |
| `header_line` | when no header date was found: the model picks one numbered line from the top of the document, and its date/place are kept only if they are visible in that line. Configured under `header_line:` (`date`, `place`, `prompt`, `max_chars`, `max_lines`); see [odwolanie.yaml](../config/doctypes/odwolanie.yaml) |
| `przez_pelnomocnika`, `prowadzacy_postepowanie`, `mocodawca_typ`, `signatories` | appeal and power-of-attorney specific flags and lists |

A new reusable step is a small Python function registered in `derive.REGISTRY`. That is the only time code changes.

## Model fallback (`llm`)

- `fill`: the rules' values win, the model fills empty fields (not `final` ones). When nothing is left to fill,
  the model is not asked.
- `model_first`: the model's values win, the rules fill gaps. Good when the layouts vary a lot. Lists come whole
  from the model; on text-layer PDFs an item is then completed from the rules' item with the same NIP, KRS, REGON
  or PESEL.
- `off`: rules only.
- Scans use `hybrid.scan_mode` from `pipeline.yaml` (default `model_first`).
- The model receives the output shape with its comments as instructions, so good comments or an `output:` block
  with meaningful example values improve its answers.
