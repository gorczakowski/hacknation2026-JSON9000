<p align="center"><img src="docs/img/json9000_stacked.png" alt="Logo JSON9000" width="420"></p>

# JSON9000 — Odwołania2JSON

Zamienia dokumenty sprawy odwoławczej przed KIO (PDF z warstwą tekstową lub skan) na JSON:
**odwołanie**, **pełnomocnictwo**, **potwierdzenie przelewu**. Każdy inny dokument dostaje `{}`.
Przygotowane na wyzwanie UZP w ramach HackNation 2026.

**Wynik na 226 treningowych plikach PDF** (60 spraw + 30 skanów + 16 dokumentów niezwiązanych), w kontenerze z 2 CPU,
jak na maszynie ewaluacyjnej: **F1 0,988**, wszystkie 226 dokumentów poprawnie sklasyfikowane, łącznie 30 minut
(szczegóły w [EXPERIMENTS.md](EXPERIMENTS.md)).

## Jak to działa

```
PDF ─► odczyt ────────────► klasyfikacja ───────► ekstrakcja ─────────► łączenie ────────────► wyliczenia + normalizacja ─► JSON
       warstwa tekstowa     ważone reguły fraz    reguły z YAML typu:   reguły + model         kod 1-12, flagi,
       (pypdfium2),         dla każdego typu      sekcje, etykiety,     (model uzupełnia       formaty (IBAN, BZP,
       OCR dla stron        (YAML); model         wartości typowane,    luki / prowadzi tam,   TED, daty), usuwanie
       bez tekstu           decyduje tylko, gdy   listy                 gdzie reguły           tytułów z nazwisk
       (MinerU/Tesseract)   reguły nie są pewne                         są słabe)
```

- **Najpierw reguły.** Każdy typ dokumentu jest opisany w jednym pliku YAML
  ([config/doctypes/](config/doctypes/)): jak go rozpoznać, gdzie są jego części i jak odczytać każde pole.
  Reguły są dokładne, wytłumaczalne, szybkie (226 PDF-ów w ok. 5 s bez OCR) i nigdy nie zmyślają wartości.
- **Mały model tam, gdzie reguły są słabe.** Model językowy o otwartych wagach (**Qwen3.5-4B**, 4,66 mld parametrów,
  Apache-2.0) jest wywoływany przez **ARK Labs** (polska firma, inferencja na serwerach w UE). Służy do:
  - **klasyfikacji**, tylko gdy reguły nie są pewne (wynik blisko progu albo dwa typy z podobnym wynikiem)
  - **pustych pól**: wygrywa odpowiedź reguł, model uzupełnia to, czego reguły nie znalazły
  - **pełnomocnictw i skanów**: wygrywa odpowiedź modelu, reguły uzupełniają luki (tam reguły są słabsze)
  - **kodu 1–12**, tylko gdy żadna reguła słów kluczowych nie pasuje do zdania o zaskarżonej czynności
  Odpowiedzi modelu są sprawdzane przed użyciem (poprawny IBAN, istniejąca data, właściwa liczba cyfr) i odrzucane,
  jeśli są nieprawdopodobne.
- **Lokalny OCR.** MinerU (wariant flash, małe modele ONNX, ok. 270 MB) działa lokalnie na CPU. Tesseract (język
  polski) jest szybszą alternatywą. Nie ma żadnego API do OCR.
- **Pochodzenie wartości.** Dla każdego dokumentu plik `evidence/<nazwa>.json` zapisuje punkty każdego typu, kto
  zdecydował (reguły czy model), a dla każdej wartości metodę i otaczający tekst, z którego pochodzi. Są tam też wyniki
  sprawdzenia cyfr kontrolnych NIP/REGON/PESEL/IBAN (nigdy nie służą do zmiany wartości).
- **Zabezpieczenia przed przekroczeniem limitu czasu.** Przez pierwsze 40 minut skany czyta MinerU, potem pozostałe
  skany czyta szybszy Tesseract, a po 50 minutach (limit to 60 minut na 150 PDF-ów) pozostałe dokumenty są
  przetwarzane wyłącznie regułami, więc każdy PDF i tak dostaje swój wynik. Przebiegi, które kończą się wcześniej,
  działają bez zmian.

## Uruchomienie

**Na serwerze ewaluacyjnym** (zob. [RUN.md](RUN.md)):
```bash
cd /opt/solution && docker compose run --rm -T --name JSON9000-BATCH json9000
```

**Lokalnie z Dockerem** (dane wejściowe w `./input`, wyniki w `./output`):
```bash
cp .env.example .env            # wpisz w nim ARK_API_KEY=... (opcjonalne: bez klucza program działa tylko na regułach)
docker compose build
docker compose run --rm -T --name JSON9000-BATCH json9000
```

**Lokalnie z Pythonem 3.13+** (bez Dockera; OCR wymaga zainstalowanego MinerU i/lub `tesseract`):
```bash
pip install -r requirements.txt
python -m odwolania2json --input input --output output
```

Przydatne opcje: `--no-llm` (tylko reguły), `--ocr-engine tesseract`, `--doctype config/examples/orzeczenie_kio.yaml`
(dodanie typu dokumentu), `--check-config` (sprawdzenie konfiguracji i zakończenie), `--strict` (niezerowy kod wyjścia,
jeśli nie udało się odczytać skanu), `-v` (szczegółowe logi).

## Interfejs w przeglądarce

Wgraj PDF (albo otwórz jeden z przykładów treningowych) i zobacz wynikowy JSON obok strony, z każdą wartością
obrysowaną w miejscu, z którego została odczytana: dokładne ramki znaków w PDF-ach z warstwą tekstową, ramki słów lub
bloków OCR na skanach. Kolory mówią, skąd pochodzi wartość: **reguła** z pliku YAML (niebieski), **model** (czerwony),
**wyliczone** z innych pól (szary). Kliknięcie pola pokazuje jego opis z szablonu, sposób odczytu i dokładną regułę
YAML. Zakładka „Typy dokumentów” dodaje typ dokumentu z pliku YAML (np.
[config/examples/orzeczenie_kio.yaml](config/examples/orzeczenie_kio.yaml)) oraz włącza i wyłącza typy; „Przetwórz
ponownie” przetwarza wtedy dokument jeszcze raz z tymi ustawieniami. Bez zmian w kodzie.

```bash
python -m odwolania2json.ui --samples hacknation-2026/dane        # http://127.0.0.1:8000
docker compose --profile ui up -d json9000-ui                      # http://<host>:8000 (ten sam obraz)
```

Interfejs używa tego samego potoku i tej samej konfiguracji co polecenie wsadowe, po jednym dokumencie. Polecenie
wsadowe nigdy go nie importuje. Opcje: `--no-llm`, `--ocr-engine`, `--doctype`, `--host 0.0.0.0`, `--port`,
`--max-mb`, `--max-pages`.

## Konfiguracja

| Plik | Co ustawia |
|---|---|
| [config/pipeline.yaml](config/pipeline.yaml) | silnik OCR i zapasowy, dostawca / model / nazwa klucza, kiedy pytany jest model (`hybrid`), zabezpieczenia czasowe, równoległość |
| [config/doctypes/*.yaml](config/doctypes/) | jeden plik na typ dokumentu, ładowane domyślnie |
| [config/templates/*.json](config/templates/) | szablony wyników od organizatorów (z komentarzami do pól, używanymi też jako instrukcje dla modelu) |
| [config/examples/](config/examples/) | dodatkowe typy dokumentów, domyślnie nieładowane (włączane przez `--doctype`) |
| `.env` | klucze API (nigdy nie trafiają do repozytorium) |

Zmiana modelu to zmiana konfiguracji, np. Ministral-14B na unijnym endpoincie Mistrala albo Qwen3.5-9B w IONOS
(zakomentowane przykłady w `pipeline.yaml`).

## Dodanie nowego typu dokumentu (bez zmian w kodzie)

Zob. **[docs/ADDING_A_DOCTYPE.md](docs/ADDING_A_DOCTYPE.md)**. W skrócie: napisz jeden plik YAML z kształtem wyniku,
frazami rozpoznającymi typ (z punktami i progiem), opcjonalnymi sekcjami oraz metodami ekstrakcji dla każdego pola.
Następnie uruchom `--check-config`, skalibruj próg na przykładowych dokumentach (`python -m odwolania2json.calibrate`)
i dodaj typ przez `--doctype`. Przykład [config/examples/orzeczenie_kio.yaml](config/examples/orzeczenie_kio.yaml)
(orzeczenia KIO) powstał właśnie w ten sposób; jego włączenie zmienia tylko wynik dla orzeczenia, a wszystkie
pozostałe wyniki zostają identyczne.

## Mapa kodu

| Moduł | Rola |
|---|---|
| [odwolania2json/__main__.py](odwolania2json/__main__.py) | wiersz poleceń, przetwarzanie równoległe, zabezpieczenie czasowe, podsumowanie |
| [reading.py](odwolania2json/reading.py) | warstwa tekstowa każdej strony, OCR dla stron bez tekstu (MinerU / Tesseract / zapasowy) |
| [pipeline.py](odwolania2json/pipeline.py) | ładowanie konfiguracji, klasyfikacja regułami, ekstrakcja regułami, listy |
| [extract.py](odwolania2json/extract.py) | metody ekstrakcji (etykieta → wartość typowana, regex, sekcje, nazwiska, miejscowość i data, …) |
| [values.py](odwolania2json/values.py) | parsery wartości typowanych: daty (polskie nazwy miesięcy), kwoty, IBAN/NRB, BZP, TED, identyfikatory |
| [hybrid.py](odwolania2json/hybrid.py) | kiedy pytany jest model, łączenie odpowiedzi reguł i modelu, sprawdzanie wiarygodności |
| [llm.py](odwolania2json/llm.py) | klient zgodny z API OpenAI, z ponawianiem i limitem równoległych zapytań |
| [derive.py](odwolania2json/derive.py) | nazwane kroki końcowe używane w konfiguracji (kod 1–12, flagi, normalizacja, osoby podpisujące) |
| [validate.py](odwolania2json/validate.py) | sprawdzanie konfiguracji przy starcie |
| [calibrate.py](odwolania2json/calibrate.py) | pomoc w doborze progu dla typów dokumentów |
| [validators.py](odwolania2json/validators.py) | cyfry kontrolne NIP / REGON / PESEL / IBAN (raportowane, nie wymuszane) |
| [ui.py](odwolania2json/ui.py) | serwer interfejsu w przeglądarce (tylko biblioteka standardowa), obrazy stron, pochodzenie pól, przełączanie typów dokumentów |
| [layout.py](odwolania2json/layout.py) | tylko dla interfejsu: geometria strony (ramki znaków / OCR) i odnajdywanie każdej wartości na stronie |

Narzędzia do ewaluacji i badań są w [research/eval/](research/eval/) (liczenie F1 według zasad organizatorów,
„odnajdywalność” wartości w OCR, benchmarki modeli i modeli decyzyjnych) oraz w [research/naive/](research/naive/)
(rozwiązania bazowe oparte wyłącznie na modelu). Notatki (po angielsku): [NOTES.md](NOTES.md) (rozpoznanie zadania),
[FAQ.md](FAQ.md) (odpowiedzi organizatorów), [MODELS.md](MODELS.md) (modele i hosty w UE),
[EXPERIMENTS.md](EXPERIMENTS.md) (wszystkie pomiary).
