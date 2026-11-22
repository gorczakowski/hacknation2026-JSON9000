# RUN - JSON9000

Run from `/opt/solution` (needs Docker; the image is already built on this server):

```bash
docker compose run --rm -T --name JSON9000 json9000
```

- Reads every PDF in `/opt/solution/input` (subfolders included) and writes one JSON per PDF to
  `/opt/solution/output`, with the same relative path and the PDF's file name (`x.pdf` → `x.json`).
- Documents that are none of the three types (odwołanie, pełnomocnictwo, potwierdzenie przelewu) get `{}`.
- Ends by itself and prints a summary (files, time, read errors, model calls).
- `/opt/solution/evidence` gets a file per document saying where each value came from (not part of the result).

## External services
- **ARK Labs API** (Ark Sp. z o.o., Szczecin, Poland; inference on servers in the EU), model
  **`arklabsai/Qwen3.5-4B`** (open weights, Apache-2.0, 4.66B parameters). It is used only as a fallback:
  rules decide where they are sure, and the model fills gaps or takes the lead on powers of attorney and scans.
- The key is in `/opt/solution/.env` as `ARK_API_KEY=...`. Without a key the program runs rules-only.
- OCR (MinerU flash, ONNX; Tesseract as an alternative) runs **locally** inside the container. No OCR API.

## If the image is missing (e.g. after `docker system prune`)
```bash
docker compose build        # needs internet: Python packages + OCR models (~1-2 GB)
docker compose run --rm -T --name JSON9000 json9000
```
