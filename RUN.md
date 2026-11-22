# RUN - JSON9000

Polecenie uruchamia się w katalogu `/opt/solution` (wymaga Dockera; obraz jest już zbudowany na tym serwerze):

```bash
docker compose run --rm -T --name JSON9000-BATCH json9000
```

- Program czyta każdy plik PDF z `/opt/solution/input` (także z podfolderów) i dla każdego zapisuje jeden plik JSON
  w `/opt/solution/output`, pod tą samą ścieżką względną i nazwą (`x.pdf` → `x.json`).
- Dokumenty spoza trzech typów (odwołanie, pełnomocnictwo, potwierdzenie przelewu) dostają `{}`.
- Program sam kończy działanie i wypisuje podsumowanie (liczba plików, czas, błędy odczytu, wywołania modelu).
- W `/opt/solution/evidence` powstaje dla każdego dokumentu plik z informacją, skąd pochodzi każda wartość. Nie jest
  on częścią wyniku.

## Usługi zewnętrzne
- **API ARK Labs** (Ark Sp. z o.o., Szczecin; serwery w UE), model **`arklabsai/Qwen3.5-4B`** (otwarte wagi,
  licencja Apache-2.0, 4,66 mld parametrów). Model pełni rolę pomocniczą: tam, gdzie reguły są pewne, decydują
  reguły; model uzupełnia braki, a przy pełnomocnictwach i skanach ma pierwszeństwo.
- Klucz jest w `/opt/solution/.env` jako `ARK_API_KEY=...`. Bez klucza działają tylko reguły.
- OCR (MinerU flash, ONNX; alternatywnie Tesseract) działa **lokalnie** w kontenerze i nie korzysta z żadnego
  zewnętrznego API.

## Jeśli obrazu nie ma (np. po `docker system prune`)
```bash
docker compose build        # wymaga internetu: pakiety Pythona i modele OCR (ok. 1-2 GB)
docker compose run --rm -T --name JSON9000-BATCH json9000
```
