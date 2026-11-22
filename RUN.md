# RUN - JSON9000

Uruchamiać z katalogu `/opt/solution` (wymaga Dockera; obraz jest już zbudowany na tym serwerze):

```bash
docker compose run --rm -T --name JSON9000-BATCH json9000
```

- Czyta każdy PDF w `/opt/solution/input` (także w podfolderach) i zapisuje jeden JSON na każdy PDF do
  `/opt/solution/output`, z tą samą ścieżką względną i nazwą pliku PDF (`x.pdf` → `x.json`).
- Dokumenty, które nie należą do żadnego z trzech typów (odwołanie, pełnomocnictwo, potwierdzenie przelewu), dostają `{}`.
- Kończy działanie samodzielnie i wypisuje podsumowanie (pliki, czas, błędy odczytu, wywołania modelu).
- W `/opt/solution/evidence` powstaje plik dla każdego dokumentu z informacją, skąd pochodzi każda wartość (nie jest
  częścią wyniku).

## Usługi zewnętrzne
- **API ARK Labs** (Ark Sp. z o.o., Szczecin; inferencja na serwerach w UE), model
  **`arklabsai/Qwen3.5-4B`** (otwarte wagi, Apache-2.0, 4,66 mld parametrów). Jest używany tylko pomocniczo:
  reguły decydują tam, gdzie są pewne, a model uzupełnia luki albo prowadzi przy pełnomocnictwach i skanach.
- Klucz jest w `/opt/solution/.env` jako `ARK_API_KEY=...`. Bez klucza program działa tylko na regułach.
- OCR (MinerU flash, ONNX; Tesseract jako alternatywa) działa **lokalnie** w kontenerze. Bez API do OCR.

## Jeśli brakuje obrazu (np. po `docker system prune`)
```bash
docker compose build        # wymaga internetu: pakiety Pythona + modele OCR (ok. 1-2 GB)
docker compose run --rm -T --name JSON9000-BATCH json9000
```
