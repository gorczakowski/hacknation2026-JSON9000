"""Run one PDF reader over a data folder and cache its output, mirroring the input layout.

Readers:
  pypdfium2            text layer only (main venv)
  mineru:<tier>:<mode> MinerU 4.x, e.g. mineru:flash:txt, mineru:flash:auto, mineru:basic:auto
                       (run with the .venv-mineru interpreter; never uses the remote service)

Output per PDF: <out>/<reader>/<rel>.txt (plain text, page breaks as form feeds)
and for MinerU also <rel>.middle.json (blocks with bboxes, for evidence).

Usage:
    .venv/Scripts/python.exe eval/read_cache.py --reader pypdfium2 --input hacknation-2026/dane
    .venv-mineru/Scripts/python.exe eval/read_cache.py --reader mineru:flash:txt --input hacknation-2026/dane
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PAGE_BREAK = "\n\f\n"


def read_pypdfium2(pdf: Path) -> tuple[str, dict | None]:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    try:
        pages = [doc[i].get_textpage().get_text_range() for i in range(len(doc))]
    finally:
        doc.close()
    return PAGE_BREAK.join(p.replace("\r\n", "\n").replace("\r", "\n") for p in pages), None


def block_text(block: dict) -> str:
    parts = []
    for c in block.get("content") or []:
        if isinstance(c, dict):
            if isinstance(c.get("content"), str):
                parts.append(c["content"])
            elif isinstance(c.get("content"), list):  # nested (e.g. list items)
                parts.append(block_text(c))
        elif isinstance(c, str):
            parts.append(c)
    return " ".join(p for p in parts if p)


def middle_to_text(mid: dict) -> str:
    pages = []
    for page in mid.get("pages", []):
        lines = [block_text(b) for b in page.get("blocks", [])]
        pages.append("\n".join(line for line in lines if line.strip()))
    return PAGE_BREAK.join(pages)


def make_mineru_reader(tier: str, mode: str):
    from mineru.parser import parse

    def read(pdf: Path):
        res = parse(str(pdf), tier=tier, ocr_mode=mode, image_analysis=False)
        mid = res.middle_json if isinstance(res.middle_json, dict) else json.loads(res.to_json())
        return middle_to_text(mid), mid

    return read


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reader", required=True)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("out/text"))
    ap.add_argument("--only", help="only PDFs whose relative path contains this string")
    ap.add_argument("--force", action="store_true", help="re-read even if cached")
    args = ap.parse_args()

    if args.reader == "pypdfium2":
        reader = read_pypdfium2
    elif args.reader.startswith("mineru:"):
        _, tier, mode = args.reader.split(":")
        reader = make_mineru_reader(tier, mode)
    else:
        print(f"unknown reader {args.reader}", file=sys.stderr)
        return 2

    out_root = args.out / args.reader.replace(":", "_")
    pdfs = sorted(p for p in args.input.rglob("*") if p.suffix.lower() == ".pdf")
    if args.only:
        pdfs = [p for p in pdfs if args.only in str(p.relative_to(args.input)).replace("\\", "/")]
    timings = {}
    for i, pdf in enumerate(pdfs, 1):
        rel = pdf.relative_to(args.input)
        txt_path = (out_root / rel).with_suffix(".txt")
        if txt_path.exists() and not args.force:
            continue
        txt_path.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        try:
            text, mid = reader(pdf)
        except Exception as e:  # noqa: BLE001
            print(f"[{i}/{len(pdfs)}] FAILED {rel}: {e}", flush=True)
            continue
        dt = time.time() - t0
        timings[str(rel).replace("\\", "/")] = round(dt, 2)
        txt_path.write_text(text, encoding="utf-8")
        if mid is not None:
            txt_path.with_suffix(".middle.json").write_text(json.dumps(mid, ensure_ascii=False), encoding="utf-8")
        print(f"[{i}/{len(pdfs)}] {rel}  {len(text)} chars  {dt:.1f}s", flush=True)
    tfile = out_root / "_timings.json"
    old = json.loads(tfile.read_text(encoding="utf-8")) if tfile.exists() else {}
    old.update(timings)
    tfile.write_text(json.dumps(old, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
