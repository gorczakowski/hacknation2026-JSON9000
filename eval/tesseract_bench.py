"""OCR benchmark for Tesseract (in Docker): render scan pages -> PNG, OCR in the uzp-tesseract container,
assemble one .txt per PDF into out/text/tesseract_psm<N>/ so eval.findability can compare it with MinerU.

Usage: python eval/tesseract_bench.py --input hacknation-2026/dane --only skany [--dpi 300] [--psm 3]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

import pypdfium2 as pdfium

PAGE_BREAK = "\n\f\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--only", default="skany")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--psm", type=int, default=3)
    ap.add_argument("--work", type=Path, default=Path("out/tess_work"))
    a = ap.parse_args()

    img_root, txt_root = a.work / "img", a.work / "txt"
    shutil.rmtree(a.work, ignore_errors=True)
    pdfs = sorted(p for p in a.input.rglob("*.pdf") if a.only in str(p.relative_to(a.input)).replace("\\", "/"))
    t0 = time.time()
    pages_per_doc = {}
    for pdf in pdfs:
        rel = pdf.relative_to(a.input).with_suffix("")
        doc = pdfium.PdfDocument(str(pdf))
        pages_per_doc[str(rel)] = len(doc)
        for i in range(len(doc)):
            out = img_root / rel / f"p{i + 1:02d}.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            doc[i].render(scale=a.dpi / 72).to_pil().convert("L").save(out)
        doc.close()
    t_render = time.time() - t0
    n_pages = sum(pages_per_doc.values())

    t1 = time.time()
    subprocess.run(["docker", "run", "--rm", "-e", f"PSM={a.psm}", "-v", f"{a.work.resolve()}:/data",
                    "uzp-tesseract", "sh", "-c",
                    "cd /data/img && find . -name '*.png' | sort | "
                    "xargs -P 4 -I{} env OMP_THREAD_LIMIT=1 sh -c 'mkdir -p /data/txt/$(dirname {}); "
                    "tesseract {} /data/txt/$(dirname {})/$(basename {} .png) -l pol --psm $PSM >/dev/null 2>&1'"],
                   check=True)
    t_ocr = time.time() - t1

    out_root = Path("out/text") / f"tesseract_psm{a.psm}"
    shutil.rmtree(out_root, ignore_errors=True)
    for rel, n in pages_per_doc.items():
        pages = []
        for i in range(n):
            f = txt_root / rel / f"p{i + 1:02d}.txt"
            pages.append(f.read_text(encoding="utf-8") if f.exists() else "")
        o = (out_root / rel).with_suffix(".txt")
        o.parent.mkdir(parents=True, exist_ok=True)
        o.write_text(PAGE_BREAK.join(pages), encoding="utf-8")
    stats = {"docs": len(pdfs), "pages": n_pages, "render_s": round(t_render, 1), "ocr_s": round(t_ocr, 1),
             "ocr_s_per_page_4procs": round(t_ocr / max(1, n_pages), 2), "ocr_s_per_doc_4procs": round(t_ocr / max(1, len(pdfs)), 2)}
    (out_root / "_stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    print(stats)


if __name__ == "__main__":
    main()
