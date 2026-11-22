# Deploying JSON9000 on the evaluation VM

Follows the organizers' [VM.md](../hacknation-2026/VM.md): Ubuntu 24.04, 2 vCPU / 16 GB, Docker preinstalled.
The evaluators run the command from `/opt/solution/RUN.md` in `/opt/solution` (with sudo, no interaction),
with the secret set in `/opt/solution/input`, and collect `/opt/solution/output`.
**Team access ends 22.11.2026 at 11:00.**

## 1. Put the project directly into /opt/solution
`input/` and `output/` already exist there and must not be removed. So pull into the existing folder instead of
cloning into a new one:
```bash
cd /opt/solution
git init -b main
git remote add origin <repo-url>        # or copy the files with scp
git pull origin main
ls RUN.md docker-compose.yml Dockerfile config odwolania2json input output
```

## 2. API key
```bash
cp .env.example .env
nano .env        # ARK_API_KEY=<the jury key>   (dedicated key with a spending limit; never committed)
chmod 600 .env
```

## 3. Build the image (downloads everything now, nothing at evaluation time)
```bash
docker compose build          # Python packages, Tesseract-pol, MinerU OCR models baked into the image
docker images json9000        # ~3.3 GB
```

## 4. Test exactly like the evaluators
```bash
rm -rf output/* evidence
cp <a few sample PDFs> input/
sudo docker compose run --rm -T --name JSON9000 json9000     # the command from RUN.md
ls output/ && cat output/*.json | head
```
- Check: every PDF has a JSON with the same name, unrelated documents have `{}`, and the summary line shows
  `failed=0, read_errors=0` and model calls without failures.
- Then `sudo reboot`, wait 1–2 minutes, log in and run the same command again. There are no background services,
  so nothing needs to start after the reboot.
- Finally clear `input/` and `output/` again: `rm -rf input/* output/* evidence`.

## 5. Notes
- Runs with no model at all if `.env` is missing or the API is down (rules only, F1 ~0.956 on training data).
- On 2 CPUs: ~30 min for our 226 training PDFs (30 of them scans). The time safety nets switch scans to Tesseract
  after 25 min and to rules only after 40 min (`config/pipeline.yaml`), so a scan-heavy set still finishes.
- Don't run `docker system prune -a` after building; it would delete the image.
