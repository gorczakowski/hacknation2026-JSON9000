# Dodanie nowego typu dokumentu

Typ dokumentu to **jeden plik YAML**. Zmiany w kodzie nie są potrzebne. W tym przewodniku używamy gotowego przykładu
[config/examples/orzeczenie_kio.yaml](../config/examples/orzeczenie_kio.yaml) (orzeczenia KIO).

## Kolejne kroki

1. **Zbierz próbki:** kilka PDF-ów nowego typu oraz folder dokumentów, które *nie* są tym typem.
2. **Skopiuj przykład** do `config/examples/<nazwa>.yaml` (albo do `config/doctypes/`, żeby ładował się domyślnie).
3. **Opisz wynik** (`output:`), **sposób rozpoznania** (`classify:`) i **sposób odczytu każdego pola** (`fields:` / `lists:`).
4. **Sprawdź konfigurację:** `python -m odwolania2json --check-config --doctype config/examples/<nazwa>.yaml`.
   Problemy są wypisywane z nazwą pliku i pola (np. nieznana sekcja, ścieżka, której nie ma w wyniku, błędny regex).
5. **Skalibruj próg:**
   ```bash
   python -m odwolania2json.calibrate --type <nazwa> --doctype config/examples/<nazwa>.yaml \
       --positives samples/<nazwa> --negatives samples/other
   ```
   Narzędzie pokazuje, jak często każda reguła frazy pasuje w każdej grupie, wynik każdego dokumentu i sugerowany próg
   w przerwie między grupami (albo ostrzeżenie, gdy grupy na siebie zachodzą).
6. **Uruchom** z `--doctype config/examples/<nazwa>.yaml` i sprawdź `output/` oraz `evidence/`, gdzie widać, skąd
   pochodzi każda wartość.

## Plik

```yaml
name: orzeczenie_kio                 # unikalna nazwa typu

output:                              # kształt JSON (klucze + zagnieżdżenie); wartości to tylko przykłady
  OrzeczenieKIO:
    sygnatura: "KIO 2617/21"
    data: "2021-09-20"
    ...
# albo zamiast tego:  template: my_type.json   (osobny plik JSON, komentarze // dozwolone; szukany obok pliku YAML,
#                                               w jego folderze templates/ albo w config/templates/)

classify:
  description: "Orzeczenie KIO (wyrok lub postanowienie) z sygnaturą akt KIO."   # dla modelu, gdy reguły nie są pewne
  threshold: 6                       # minimalny wynik, żeby dokument był tym typem
  sure_margin: 2                     # opcjonalne: reguły decydują same, gdy wynik >= threshold + ta wartość (domyślna z pipeline.yaml)
  priority: 0                        # opcjonalne: rozstrzyga dokładne remisy między typami
  rules:                             # frazy z punktami; dopasowanie bez rozróżniania wielkości liter i polskich znaków
    - {pattern: '^\s*sygn\.\s*akt\s*:?\s*KIO', weight: 4}
    - {pattern: '^\s*(wyrok|postanowienie)\s*$', weight: 3}
    - {pattern: '^\s*orzeka\s*:?', weight: 2, anywhere: true}   # domyślnie: pierwsze 4000 znaków; anywhere: cały tekst
    - {pattern: '^\s*odwołujący\s*:', weight: -4}               # punkty ujemne dla podobnych dokumentów

sections:                            # opcjonalne nazwane części dokumentu
  sentencja:
    start: ['^\s*orzeka\s*:?']       # pierwsze dopasowanie rozpoczyna sekcję
    end: ['^\s*stosownie\s+do', '^\s*uzasadnienie']   # sekcja kończy się też na początku każdej innej sekcji
    max_chars: 3000

fields:                              # ścieżka w wyniku -> metody ekstrakcji, próbowane po kolei; wygrywa pierwsza wartość
  OrzeczenieKIO.sygnatura:
    - {regex: 'sygn\.\s*akt\s*:?\s*(?P<v>KIO\s*\d+/\d+)'}
  OrzeczenieKIO.data:
    - {regex: 'z\s+dnia\s+(?P<v>\d{1,2}\s+\w+\s+\d{4})', type: date}
  OrzeczenieKIO.rozstrzygniecie:
    - {regex: '^\s*1\.\s*(?P<v>[^;\n]+)', scope: sentencja}   # szukanie tylko wewnątrz sekcji
  OrzeczenieKIO.numer_bzp:
    - {find: bzp}

derive: [normalize]                  # nazwane kroki końcowe (zob. niżej)

llm:
  mode: fill                         # fill | model_first | off  (jak łączone są reguły i model)
  instructions: ""                   # opcjonalne dodatkowe instrukcje dla modelu
```

## Rozpoznawanie (`classify`)

- Wynik to suma wag reguł, które pasują.
- Typ się kwalifikuje, gdy jego wynik osiąga `threshold`. Spośród kwalifikujących się typów wygrywa ten z najwyższym
  wynikiem.
- Reguły są **pewne**, gdy zwycięzca ma co najmniej `sure_margin` powyżej swojego progu i co najmniej `sure_margin`
  przewagi nad każdym innym typem. Wtedy model nie jest wywoływany.
- Gdy każdy typ ma wynik 0 lub mniej, dokument jest niezwiązany (`{}`).
- W pozostałych przypadkach wybiera model, na podstawie zdań `description` poszczególnych typów. Jeśli model nie jest
  skonfigurowany, używana jest odpowiedź reguł.

## Metody ekstrakcji

| Metoda | Przykład | Znaczenie |
|---|---|---|
| `label` + `type` | `{label: ['kwota\s+operacji', 'kwota'], type: amount}` | wartość tuż po jednej z etykiet (wygrywa pierwsza etykieta, która daje wartość) |
| `regex` | `{regex: 'NIP\s*(?P<v>\d{10})'}` | nazwana grupa `v` (albo grupa 1); opcjonalny `type` do sparsowania; `dotall: true` |
| `find` | `{find: email}` | pierwsza wartość danego rodzaju w dowolnym miejscu zakresu: `email`, `bzp`, `ted`, `date` |
| `place_date` | `{place_date: date}` / `{place_date: place}` | z wiersza nagłówka w rodzaju „Gdańsk, dnia 15 lipca 2026 r.” (`date_only_before: '<regex nagłówka>'`: także wiersz z samą datą na górze dokumentu albo tuż nad tym nagłówkiem) |
| `first_line` | `{first_line: true, skip: '^Konsorcjum'}` | pierwszy niepusty wiersz zakresu (np. nazwa firmy po „Odwołujący:”) |
| `names_after` | `{names_after: ['udzielam\s+pełnomocnictwa'], first: true}` | imiona i nazwiska po frazie, bez tytułów (r. pr., adw.) |
| `nth` | `{nth: 1, of: account, min_count: 2}` | n-ty numer rachunku w zakresie (rozwiązanie zapasowe, gdy nie ma etykiet) |
| `const` | `{const: "osoba_prawna"}` | stała wartość |

- Każda metoda może przyjąć `scope: <sekcja>`. Jeśli tej sekcji nie ma w dokumencie, metoda jest pomijana i próbowana
  jest następna.
- `final: true` czyni metodę rozstrzygającą: gdy może zostać użyta (bez `scope` albo jej sekcja została znaleziona), jej
  odpowiedź obowiązuje, nawet jeśli nic nie znalazła. Kolejne metody są pomijane, a w trybie `fill` model nie uzupełnia
  tego pola. Przydaje się, gdy brak wartości we właściwym miejscu oznacza „brak wartości”, np. numery ogłoszeń czytane
  tylko z bloku „dotyczy”, bo treść może przywoływać inne postępowania. Działa tylko dla PDF-ów z warstwą tekstową; dla
  tekstu z OCR jest ignorowane, bo OCR może oddzielić etykietę od wartości.
- Wartości `type`: `date` (ISO; obsługiwane polskie nazwy miesięcy), `amount` (liczba zmiennoprzecinkowa), `account`
  (IBAN z kodem kraju albo 26-cyfrowy NRB), `digitsN` (np. `digits10`), `email`, `token`, `bzp`, `ted`, `text`
  (wielowierszowy, do etykiet z `stop:`), `line`.

**Listy** (np. kilku pełnomocników, członkowie konsorcjum) umieszcza się w `lists:`:
```yaml
lists:
  Zamawiajacy:                  # ścieżka listy w wyniku
    scope: zamawiajacy          # sekcja, z której czytamy
    split: '^\s*\d\)\s'         # opcjonalne: podział sekcji na elementy („1) ...”, „2) ...”)
    max_items: 3
    fields:                     # ścieżki wewnątrz jednego elementu
      nazwa: [{first_line: true}]
      nip:   [{label: ['NIP'], type: digits10}]
```
Albo jeden element na każde znalezione nazwisko: `from_names: [...]` z `name_field: imie_nazwisko`.

## Kroki końcowe (`derive`)

Nazwane kroki z wbudowanej biblioteki, stosowane po kolei:

| Krok | Co robi |
|---|---|
| `normalize` | identyfikatory do samych cyfr; numery rachunków bez spacji, z zachowaniem kodu kraju; formaty BZP i TED; kwoty dodatnie; tytuły usunięte z imion i nazwisk |
| `powod_and_date` | kod zaskarżonej czynności 1–12. Konfigurowany w tym samym pliku YAML w `powod:` (wzorce fragmentu, reguły słów kluczowych, etykiety); zob. [odwolanie.yaml](../config/doctypes/odwolanie.yaml) |
| `header_line` | gdy nie znaleziono daty w nagłówku: model wybiera jeden ponumerowany wiersz z początku dokumentu, a data i miejscowość zostają tylko wtedy, gdy są widoczne w tym wierszu. Konfigurowany w `header_line:` (`date`, `place`, `prompt`, `max_chars`, `max_lines`); zob. [odwolanie.yaml](../config/doctypes/odwolanie.yaml) |
| `przez_pelnomocnika`, `prowadzacy_postepowanie`, `mocodawca_typ`, `signatories` | flagi i listy właściwe dla odwołania i pełnomocnictwa |

Nowy krok wielokrotnego użytku to mała funkcja w Pythonie zarejestrowana w `derive.REGISTRY`. Tylko wtedy zmienia się
kod.

## Model jako uzupełnienie (`llm`)

- `fill`: wygrywają wartości z reguł, model uzupełnia puste pola (poza polami `final`). Gdy nie ma nic do uzupełnienia,
  model nie jest pytany.
- `model_first`: wygrywają wartości modelu, reguły uzupełniają luki. Dobre, gdy układy dokumentów bardzo się różnią.
  Listy pochodzą w całości od modelu; w PDF-ach z warstwą tekstową element jest wtedy uzupełniany z elementu reguł
  o tym samym NIP, KRS, REGON lub PESEL.
- `off`: tylko reguły.
- Skany używają `hybrid.scan_mode` z `pipeline.yaml` (domyślnie `model_first`).
- Model dostaje kształt wyniku wraz z komentarzami jako instrukcje, więc dobre komentarze albo blok `output:`
  z sensownymi przykładowymi wartościami poprawiają jego odpowiedzi.
