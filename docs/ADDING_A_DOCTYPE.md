# Dodawanie nowego typu dokumentu

Typ dokumentu to **jeden plik YAML**. Nie trzeba zmieniać kodu. Przewodnik opiera się na gotowym przykładzie
[config/examples/orzeczenie_kio.yaml](../config/examples/orzeczenie_kio.yaml) (orzeczenia KIO).

## Krok po kroku

1. **Zbierz próbki:** kilka plików PDF nowego typu oraz folder z dokumentami, które tym typem *nie* są.
2. **Skopiuj przykład** do `config/examples/<nazwa>.yaml` (albo do `config/doctypes/`, jeśli typ ma być ładowany
   domyślnie).
3. **Opisz** strukturę wyniku (`output:`), po czym rozpoznać dokument (`classify:`) i jak odczytać każde pole
   (`fields:` / `lists:`).
4. **Sprawdź konfigurację:** `python -m odwolania2json --check-config --doctype config/examples/<nazwa>.yaml`.
   Błędy są wypisywane z nazwą pliku i pola (np. nieznana sekcja, ścieżka, której nie ma w wyniku, błędny regex).
5. **Dobierz próg:**
   ```bash
   python -m odwolania2json.calibrate --type <nazwa> --doctype config/examples/<nazwa>.yaml \
       --positives samples/<nazwa> --negatives samples/other
   ```
   Narzędzie pokazuje, jak często każda fraza występuje w obu grupach, wynik punktowy każdego dokumentu i proponowany
   próg pomiędzy grupami (albo ostrzeżenie, gdy wyniki grup się nakładają).
6. **Uruchom** program z `--doctype config/examples/<nazwa>.yaml` i sprawdź `output/` oraz `evidence/`. W `evidence/`
   widać, skąd pochodzi każda wartość.

## Budowa pliku

```yaml
name: orzeczenie_kio                 # unikalna nazwa typu

output:                              # struktura JSON (klucze i zagnieżdżenie); wartości są tylko przykładami
  OrzeczenieKIO:
    sygnatura: "KIO 2617/21"
    data: "2021-09-20"
    ...
# albo zamiast tego:  template: my_type.json   (osobny plik JSON, komentarze // dozwolone; szukany obok pliku YAML,
#                                               w jego folderze templates/ albo w config/templates/)

classify:
  description: "Orzeczenie KIO (wyrok lub postanowienie) z sygnaturą akt KIO."   # dla modelu, gdy reguły nie są pewne
  threshold: 6                       # minimalna liczba punktów, by uznać dokument za ten typ
  sure_margin: 2                     # opcjonalnie: reguły decydują same, gdy wynik >= threshold + ta wartość (domyślna w pipeline.yaml)
  priority: 0                        # opcjonalnie: rozstrzyga remisy między typami
  rules:                             # frazy z punktami; wielkość liter i polskie znaki nie mają znaczenia
    - {pattern: '^\s*sygn\.\s*akt\s*:?\s*KIO', weight: 4}
    - {pattern: '^\s*(wyrok|postanowienie)\s*$', weight: 3}
    - {pattern: '^\s*orzeka\s*:?', weight: 2, anywhere: true}   # domyślnie pierwsze 4000 znaków; anywhere: cały tekst
    - {pattern: '^\s*odwołujący\s*:', weight: -4}               # punkty ujemne dla dokumentów łudząco podobnych

sections:                            # opcjonalne, nazwane części dokumentu
  sentencja:
    start: ['^\s*orzeka\s*:?']       # sekcja zaczyna się od pierwszego dopasowania
    end: ['^\s*stosownie\s+do', '^\s*uzasadnienie']   # kończy się tutaj albo tam, gdzie zaczyna się inna sekcja
    max_chars: 3000

fields:                              # ścieżka w wyniku -> metody odczytu, próbowane po kolei; liczy się pierwsza znaleziona wartość
  OrzeczenieKIO.sygnatura:
    - {regex: 'sygn\.\s*akt\s*:?\s*(?P<v>KIO\s*\d+/\d+)'}
  OrzeczenieKIO.data:
    - {regex: 'z\s+dnia\s+(?P<v>\d{1,2}\s+\w+\s+\d{4})', type: date}
  OrzeczenieKIO.rozstrzygniecie:
    - {regex: '^\s*1\.\s*(?P<v>[^;\n]+)', scope: sentencja}   # szuka tylko w tej sekcji
  OrzeczenieKIO.numer_bzp:
    - {find: bzp}

derive: [normalize]                  # nazwane kroki przetwarzania końcowego (opis niżej)

llm:
  mode: fill                         # fill | model_first | off  (jak łączyć reguły z modelem)
  instructions: ""                   # opcjonalne dodatkowe instrukcje dla modelu
```

## Rozpoznawanie (`classify`)

- Wynik typu to suma wag reguł, które pasują do dokumentu.
- Typ wchodzi w grę, gdy jego wynik osiąga próg (`threshold`). Spośród takich typów wygrywa ten z najwyższym wynikiem.
- Reguły są **pewne**, gdy zwycięzca przekracza swój próg co najmniej o `sure_margin` i ma co najmniej `sure_margin`
  punktów przewagi nad każdym innym typem. Wtedy model nie jest wywoływany.
- Gdy wszystkie typy mają 0 punktów lub mniej, dokument nie należy do żadnego typu (`{}`).
- W pozostałych przypadkach decyduje model, na podstawie opisów typów (`description`). Jeśli model nie jest
  skonfigurowany, obowiązuje odpowiedź reguł.

## Metody odczytu

| Metoda | Przykład | Działanie |
|---|---|---|
| `label` + `type` | `{label: ['kwota\s+operacji', 'kwota'], type: amount}` | wartość tuż po jednej z etykiet (liczy się pierwsza etykieta, po której jest wartość) |
| `regex` | `{regex: 'NIP\s*(?P<v>\d{10})'}` | grupa nazwana `v` (albo grupa 1); opcjonalny `type` określa, jak odczytać wartość; `dotall: true` |
| `find` | `{find: email}` | pierwsza wartość danego rodzaju w dowolnym miejscu zakresu: `email`, `bzp`, `ted`, `date` |
| `place_date` | `{place_date: date}` / `{place_date: place}` | z wiersza nagłówka w rodzaju „Gdańsk, dnia 15 lipca 2026 r.” (`date_only_before: '<regex nagłówka>'`: także z wiersza zawierającego samą datę, na górze dokumentu albo tuż nad tym nagłówkiem) |
| `first_line` | `{first_line: true, skip: '^Konsorcjum'}` | pierwszy niepusty wiersz zakresu (np. nazwa firmy po „Odwołujący:”) |
| `names_after` | `{names_after: ['udzielam\s+pełnomocnictwa'], first: true}` | imiona i nazwiska po podanej frazie, bez tytułów (r. pr., adw.) |
| `nth` | `{nth: 1, of: account, min_count: 2}` | n-ty numer rachunku w zakresie (na wypadek, gdy nie ma etykiet) |
| `const` | `{const: "osoba_prawna"}` | stała wartość |

- Zakres to domyślnie cały dokument. Każda metoda może go zawęzić do sekcji: `scope: <sekcja>`. Jeśli tej sekcji nie
  ma w dokumencie, metoda jest pomijana i program próbuje następnej.
- `final: true` oznacza, że metoda ma ostatnie słowo: jeśli można ją zastosować (nie ma `scope` albo sekcja została
  znaleziona), jej wynik obowiązuje, nawet gdy nic nie znalazła. Kolejne metody są pomijane, a w trybie `fill` model
  nie uzupełnia tego pola. Przydaje się, gdy brak wartości we właściwym miejscu oznacza, że wartości po prostu nie ma.
  Przykład: numery ogłoszeń czytane tylko z bloku „dotyczy”, bo dalsza treść może przywoływać inne postępowania.
  Działa tylko dla plików z warstwą tekstową; dla tekstu z OCR jest ignorowane, bo OCR potrafi rozdzielić etykietę
  i wartość.
- Wartości `type`: `date` (format ISO; polskie nazwy miesięcy są obsługiwane), `amount` (liczba
  zmiennoprzecinkowa), `account` (IBAN z kodem kraju albo 26-cyfrowy NRB), `digitsN` (np. `digits10`), `email`,
  `token`, `bzp`, `ted`, `text` (tekst wielowierszowy, do etykiet podanych w `stop:`), `line`.

**Listy** (np. kilku pełnomocników albo członkowie konsorcjum) opisuje się w `lists:`:
```yaml
lists:
  Zamawiajacy:                  # ścieżka listy w wyniku
    scope: zamawiajacy          # sekcja, z której czytamy
    split: '^\s*\d\)\s'         # opcjonalnie: podział sekcji na elementy („1) ...”, „2) ...”)
    max_items: 3
    fields:                     # ścieżki wewnątrz jednego elementu
      nazwa: [{first_line: true}]
      nip:   [{label: ['NIP'], type: digits10}]
```
Można też tworzyć jeden element dla każdej znalezionej osoby: `from_names: [...]` z `name_field: imie_nazwisko`.

## Przetwarzanie końcowe (`derive`)

Nazwane kroki z wbudowanej biblioteki, wykonywane po kolei:

| Krok | Działanie |
|---|---|
| `normalize` | identyfikatory zapisane samymi cyframi; numery rachunków bez spacji, z zachowaniem kodu kraju; formaty BZP i TED; kwoty dodatnie; imiona i nazwiska bez tytułów |
| `powod_and_date` | kod zaskarżonej czynności (1–12). Konfigurowany w tym samym pliku YAML, w `powod:` (wzorce szukanego fragmentu, reguły słów kluczowych, etykiety); zob. [odwolanie.yaml](../config/doctypes/odwolanie.yaml) |
| `header_line` | gdy w nagłówku nie znaleziono daty: model wskazuje jeden z ponumerowanych wierszy z początku dokumentu, a data i miejscowość zostają tylko wtedy, gdy rzeczywiście są w tym wierszu. Konfigurowany w `header_line:` (`date`, `place`, `prompt`, `max_chars`, `max_lines`); zob. [odwolanie.yaml](../config/doctypes/odwolanie.yaml) |
| `przez_pelnomocnika`, `prowadzacy_postepowanie`, `mocodawca_typ`, `signatories` | flagi i listy specyficzne dla odwołania i pełnomocnictwa |

Nowy, ogólny krok to mała funkcja w Pythonie zarejestrowana w `derive.REGISTRY`. To jedyna sytuacja, w której trzeba
zmienić kod.

## Udział modelu (`llm`)

- `fill`: pierwszeństwo mają reguły, model uzupełnia puste pola (z wyjątkiem pól `final`). Gdy nie ma czego uzupełniać,
  model nie jest pytany.
- `model_first`: pierwszeństwo ma model, reguły uzupełniają braki. Sprawdza się, gdy układ dokumentów bywa bardzo
  różny. Listy pochodzą wtedy w całości od modelu; w plikach z warstwą tekstową każdy element listy jest uzupełniany
  danymi z elementu znalezionego przez reguły, który ma ten sam NIP, KRS, REGON lub PESEL.
- `off`: tylko reguły.
- Dla skanów obowiązuje `hybrid.scan_mode` z `pipeline.yaml` (domyślnie `model_first`).
- Model dostaje strukturę wyniku razem z komentarzami jako instrukcję. Dobre komentarze albo blok `output:`
  z sensownymi przykładowymi wartościami poprawiają więc jego odpowiedzi.
