#!/usr/bin/env bash
# Run the naive chat pipeline for each model given, then normalize+score. Usage: research/eval/run_models.sh model1 model2 ...
cd "$(dirname "$0")/../.."
for m in "$@"; do
  tag=$(echo "$m" | tr '/:.' '___')
  out="${MODELS_OUT:-out/models}/$tag"; mkdir -p "${MODELS_OUT:-out/models}"
  .venv/Scripts/python.exe research/naive/naive.py --input hacknation-2026/dane --output "$out" --model "$m" --workers ${WORKERS:-8} > "$out.log" 2>&1
  .venv/Scripts/python.exe research/eval/renorm.py "$out" > /dev/null
  echo "=== $m  $(tail -1 "$out.log")"
  .venv/Scripts/python.exe -m research.eval.score --gold hacknation-2026/dane --pred "${out}_norm" --mode strict --json "${out}_score.json" | sed -n '2p;8,9p'
done
