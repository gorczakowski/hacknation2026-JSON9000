#!/usr/bin/env bash
# Item 1: models read MinerU OCR text (not page images) for the 30 scans. Usage: research/eval/run_scans_ocr.sh model...
cd "$(dirname "$0")/../.."
for m in "$@"; do
  tag=$(echo "$m" | tr '/:.' '___'); out="out/scans_ocr/$tag"; mkdir -p out/scans_ocr
  .venv/Scripts/python.exe research/naive/naive.py --input hacknation-2026/dane --output "$out" --model "$m" --only skany --ocr-text out/text/mineru_flash_auto --workers 4 > "$out.log" 2>&1
  .venv/Scripts/python.exe research/eval/renorm.py "$out" > /dev/null
  .venv/Scripts/python.exe -m research.eval.score --gold hacknation-2026/dane --pred "${out}_norm" --only skany --mode strict --json "${out}_score.json" > /dev/null
  .venv/Scripts/python.exe -c "import json;s=json.load(open('${out}_score.json'))['strict'];print('%-40s scans-via-OCR F1=%.3f  %s'%('$m',s['f1'],open('$out.log',encoding='utf-8',errors='ignore').read().strip().splitlines()[-1][-60:]))"
done
