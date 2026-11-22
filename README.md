<p align="center"><img src="docs/img/json9000_stacked.png" alt="Logo JSON9000" width="420"></p>

# JSON9000 — Odwołania2JSON

Program zamienia dokumenty ze sprawy odwoławczej przed KIO (pliki PDF z tekstem albo skany) na pliki JSON. Rozpoznaje
trzy typy dokumentów: **odwołanie**, **pełnomocnictwo** i **potwierdzenie przelewu**. Dla każdego innego dokumentu
zapisuje `{}`. Rozwiązanie zadania UZP na HackNation 2026.

**Wynik na 226 plikach PDF ze zbioru treningowego** (60 spraw, 30 skanów, 16 dokumentów niezwiązanych ze sprawą),
w kontenerze z 2 procesorami, tak jak na maszynie ewaluacyjnej: **F1 0,988**, wszystkie 226 dokumentów poprawnie
rozpoznane, całość w 30 minut (szczegóły w [EXPERIMENTS.md](EXPERIMENTS.md)).

## Jak to działa

```
PDF ─► odczyt ────────────► klasyfikacja ───────► ekstrakcja ─────────► łączenie ────────────► wyliczenia + normalizacja ─► JSON
       warstwa tekstowa     ważone reguły fraz    reguły z YAML typu:   reguły + model         kod 1-12, flagi,
       (pypdfium2),         dla każdego typu      sekcje, etykiety,     (model uzupełnia       formaty (IBAN, BZP,
       OCR dla stron        (YAML); model         wartości typowane,    luki / prowadzi tam,   TED, daty), usuwanie
       bez tekstu           decyduje tylko, gdy   listy                 gdzie reguły           tytułów z nazwisk
       (MinerU/Tesseract)   reguły nie są pewne                         są słabe)
```

- **Najpierw reguły.** Każdy typ dokumentu jest opisany w jednym pliku YAML ([config/doctypes/](config/doctypes/)):
  po czym go rozpoznać, gdzie są jego części i jak odczytać każde pole. Reguły są precyzyjne, łatwe do wyjaśnienia,
  szybkie (184 pliki PDF z warstwą tekstową w niecałe 4 sekundy) i nigdy nie wymyślają wartości.
- **Mały model tam, gdzie reguły nie wystarczają.** Korzystamy z otwartego modelu językowego **Qwen3.5-4B**
  (4,66 mld parametrów, licencja Apache-2.0), udostępnianego przez **ARK Labs** (polska firma, serwery w UE).
  Model jest używany:
  - **do klasyfikacji**, tylko gdy reguły nie są pewne (wynik blisko progu albo dwa typy z podobnym wynikiem);
  - **do uzupełniania pustych pól**: pierwszeństwo mają reguły, model wypełnia tylko to, czego reguły nie znalazły;
  - **przy pełnomocnictwach i skanach**: pierwszeństwo ma model, a reguły uzupełniają braki (w tych dokumentach
    reguły radzą sobie gorzej);
  - **do ustalenia kodu czynności (1–12)**, tylko gdy żadna reguła oparta na słowach kluczowych nie pasuje do zdania
    opisującego zaskarżoną czynność.

  Każda odpowiedź modelu jest sprawdzana (poprawny IBAN, prawdziwa data, właściwa liczba cyfr), a wartości
  nieprawdopodobne są odrzucane.
- **OCR lokalnie.** MinerU (wersja flash, małe modele ONNX, ok. 270 MB) działa na procesorze w kontenerze.
  Szybszą alternatywą jest Tesseract z polskim modelem języka. OCR nie korzysta z żadnego zewnętrznego API.
- **Skąd pochodzi każda wartość.** Dla każdego dokumentu powstaje plik `evidence/<nazwa>.json`: punktacja każdego typu,
  kto podjął decyzję (reguły czy model), a dla każdej wartości metoda odczytu i fragment tekstu, z którego ją wzięto.
  Plik zawiera też wynik sprawdzenia cyfr kontrolnych NIP, REGON, PESEL i IBAN (tylko informacyjnie: na tej podstawie
  wartości nie są zmieniane).
- **Zabezpieczenia czasowe.** Przez pierwsze 40 minut skany odczytuje MinerU, później szybszy Tesseract, a po
  50 minutach (limit to 60 minut na 150 plików PDF) pozostałe dokumenty są przetwarzane już tylko regułami. Dzięki temu
  każdy plik PDF dostaje wynik. Przebiegów, które kończą się wcześniej, to nie dotyczy.

## Uruchomienie

**Na serwerze ewaluacyjnym** (zob. [RUN.md](RUN.md)):
```bash
cd /opt/solution && docker compose run --rm -T --name JSON9000-BATCH json9000
```

**Lokalnie w Dockerze** (pliki wejściowe w `./input`, wyniki w `./output`):
```bash
cp .env.example .env            # wpisz ARK_API_KEY=... (opcjonalnie; bez klucza działają tylko reguły)
docker compose build
docker compose run --rm -T --name JSON9000-BATCH json9000
```

**Lokalnie w Pythonie 3.13+** (bez Dockera; do OCR potrzebny jest zainstalowany MinerU i/lub `tesseract`):
```bash
pip install -r requirements.txt
python -m odwolania2json --input input --output output
```

Przydatne opcje: `--no-llm` (tylko reguły), `--ocr-engine tesseract`, `--doctype config/examples/orzeczenie_kio.yaml`
(dodatkowy typ dokumentu), `--check-config` (tylko sprawdza konfigurację), `--strict` (kod wyjścia różny od zera, gdy
nie udało się odczytać skanu), `-v` (więcej szczegółów w logu).

## Interfejs w przeglądarce

Wgraj plik PDF (albo otwórz jeden z przykładów treningowych), a obok strony zobaczysz wynikowy JSON. Każda wartość jest
zaznaczona na stronie w miejscu, z którego ją odczytano: w plikach z warstwą tekstową dokładnie co do znaku, na skanach
według słów lub bloków z OCR. Kolor mówi, skąd pochodzi wartość: **reguła** z pliku YAML (niebieski), **model**
(czerwony) albo **wyliczone** z innych pól (szary). Po kliknięciu pola widać jego opis z szablonu, sposób odczytu
i dokładną regułę z YAML. W zakładce „Typy dokumentów” można dodać typ z pliku YAML (np.
[config/examples/orzeczenie_kio.yaml](config/examples/orzeczenie_kio.yaml)) oraz włączać i wyłączać typy, a przycisk
„Przetwórz ponownie” przetwarza dokument jeszcze raz z nowymi ustawieniami. Nie wymaga to żadnych zmian w kodzie.

```bash
python -m odwolania2json.ui --samples hacknation-2026/dane        # http://127.0.0.1:8000
docker compose --profile ui up -d json9000-ui                      # http://<host>:8000 (ten sam obraz)
```

Interfejs korzysta z tego samego przetwarzania i tej samej konfiguracji co tryb wsadowy, tylko dla pojedynczego
dokumentu. Tryb wsadowy w ogóle go nie importuje. Opcje: `--no-llm`, `--ocr-engine`, `--doctype`, `--host 0.0.0.0`,
`--port`, `--max-mb`, `--max-pages`.

## Konfiguracja

| Plik | Co ustawia |
|---|---|
| [config/pipeline.yaml](config/pipeline.yaml) | silnik OCR i silnik zapasowy; dostawcę, model i nazwę zmiennej z kluczem API; kiedy pytać model (`hybrid`); zabezpieczenia czasowe; równoległość |
| [config/doctypes/*.yaml](config/doctypes/) | po jednym pliku na typ dokumentu, ładowane domyślnie |
| [config/templates/*.json](config/templates/) | szablony wyników od organizatorów (komentarze do pól służą też jako instrukcje dla modelu) |
| [config/examples/](config/examples/) | dodatkowe typy dokumentów, domyślnie wyłączone (włącza się je opcją `--doctype`) |
| `.env` | klucze API (nigdy nie trafiają do repozytorium) |

Zmiana modelu wymaga tylko zmiany konfiguracji, np. na Ministral-14B przez unijny endpoint Mistrala albo na
Qwen3.5-9B w IONOS (zakomentowane przykłady są w `pipeline.yaml`).

## Nowy typ dokumentu bez zmian w kodzie

Szczegóły: **[docs/ADDING_A_DOCTYPE.md](docs/ADDING_A_DOCTYPE.md)**. W skrócie: piszesz jeden plik YAML ze strukturą
wyniku, frazami, po których rozpoznaje się typ (każda z liczbą punktów, do tego próg), opcjonalnymi sekcjami
i sposobem odczytu każdego pola. Potem uruchamiasz `--check-config`, dobierasz próg na przykładowych dokumentach
(`python -m odwolania2json.calibrate`) i dodajesz typ opcją `--doctype`. Tak powstał przykład
[config/examples/orzeczenie_kio.yaml](config/examples/orzeczenie_kio.yaml) (orzeczenia KIO). Po jego włączeniu
zmieniają się tylko wyniki dla orzeczeń, a wszystkie pozostałe zostają bez zmian.

## Mapa kodu

| Moduł | Rola |
|---|---|
| [odwolania2json/__main__.py](odwolania2json/__main__.py) | wiersz poleceń, przetwarzanie równoległe, zabezpieczenia czasowe, podsumowanie |
| [reading.py](odwolania2json/reading.py) | tekst z każdej strony, OCR dla stron bez tekstu (MinerU / Tesseract / silnik zapasowy) |
| [pipeline.py](odwolania2json/pipeline.py) | wczytywanie konfiguracji, klasyfikacja i ekstrakcja regułami, listy |
| [extract.py](odwolania2json/extract.py) | metody ekstrakcji (etykieta → wartość danego typu, regex, sekcje, imiona i nazwiska, miejscowość i data, …) |
| [values.py](odwolania2json/values.py) | odczyt wartości: daty (z polskimi nazwami miesięcy), kwoty, IBAN/NRB, BZP, TED, identyfikatory |
| [hybrid.py](odwolania2json/hybrid.py) | kiedy pytać model, łączenie odpowiedzi reguł i modelu, sprawdzanie wiarygodności |
| [llm.py](odwolania2json/llm.py) | klient API zgodnego z OpenAI, z ponawianiem zapytań i limitem zapytań równoległych |
| [derive.py](odwolania2json/derive.py) | nazwane kroki przetwarzania końcowego używane w konfiguracji (kod 1–12, flagi, normalizacja, osoby podpisujące) |
| [validate.py](odwolania2json/validate.py) | sprawdzanie konfiguracji przy starcie |
| [calibrate.py](odwolania2json/calibrate.py) | pomoc w doborze progu dla typu dokumentu |
| [validators.py](odwolania2json/validators.py) | cyfry kontrolne NIP / REGON / PESEL / IBAN (tylko raportowane) |
| [ui.py](odwolania2json/ui.py) | serwer interfejsu w przeglądarce (tylko biblioteka standardowa), obrazy stron, pochodzenie wartości, przełączanie typów dokumentów |
| [layout.py](odwolania2json/layout.py) | tylko dla interfejsu: geometria strony (położenie znaków i bloków OCR) i odnajdywanie wartości na stronie |

Narzędzia do oceny i badań są w [research/eval/](research/eval/) (liczenie F1 według zasad organizatorów, sprawdzanie,
czy wartości da się znaleźć w tekście z OCR, porównania modeli) oraz w [research/naive/](research/naive/) (rozwiązania
porównawcze oparte wyłącznie na modelu). Notatki (po angielsku): [NOTES.md](NOTES.md) (analiza zadania),
[FAQ.md](FAQ.md) (odpowiedzi organizatorów), [MODELS.md](MODELS.md) (modele i dostawcy w UE),
[EXPERIMENTS.md](EXPERIMENTS.md) (wszystkie pomiary).
