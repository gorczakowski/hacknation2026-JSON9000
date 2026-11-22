#!/bin/sh
# OCR every PNG under /data/img into /data/txt (same layout), page files p01.png.. -> one doc.txt per folder
cd /data/img
find . -name '*.png' | sort | while read f; do
  out="/data/txt/${f%.png}"; mkdir -p "$(dirname "$out")"
  s=$(date +%s.%N); tesseract "$f" "$out" -l pol --psm ${PSM:-3} >/dev/null 2>&1; e=$(date +%s.%N)
  echo "$f $(echo "$e - $s" | bc 2>/dev/null || echo ?)"
done
