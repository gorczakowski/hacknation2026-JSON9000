"""Pick a classification threshold (and check the rules) for one document type from sample documents.

Give it example PDFs of the type (--positives) and PDFs that are NOT this type (--negatives). It prints:
  - how often each rule matches in each group (a good rule fires on positives, not on negatives)
  - every document's score
  - a suggested threshold: the middle of the gap between the lowest positive and the highest negative score,
    and a warning if the two groups overlap (then the rules need another distinguishing pattern)

Usage:
  python -m odwolania2json.calibrate --type orzeczenie_kio --doctype config/examples/orzeczenie_kio.yaml \
      --positives samples/orzeczenia --negatives hacknation-2026/dane
"""

from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

from . import extract as X
from .pipeline import load_config
from .reading import load_document

ROOT = Path(__file__).resolve().parent.parent


def score_rules(doc, rules, head_chars):
    head, full = X.fold(doc.norm[:head_chars]), X.fold(doc.norm)
    hits = [bool(re.search(X.fold(r["pattern"]), full if r.get("anywhere") else head, re.I | re.M)) for r in rules]
    return sum(r["weight"] for r, h in zip(rules, hits) if h), hits


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--type", required=True, help="document type name (as in its YAML 'name:')")
    ap.add_argument("--positives", type=Path, required=True, help="folder with example PDFs of this type")
    ap.add_argument("--negatives", type=Path, required=True, help="folder with PDFs of other types / unrelated")
    ap.add_argument("--config", type=Path, default=ROOT / "config")
    ap.add_argument("--doctype", type=Path, action="append", default=[], help="extra doctype YAML (as in the CLI)")
    ap.add_argument("--ocr-cache", type=Path, help="cached OCR text for scans (<rel>.txt), optional")
    a = ap.parse_args(argv)

    pipeline_cfg, doctypes = load_config(a.config, a.doctype)
    dt = next((d for d in doctypes if d.name == a.type), None)
    if dt is None:
        print(f"type '{a.type}' not found; loaded: {[d.name for d in doctypes]}")
        return 2
    rules = dt.cfg["classify"]["rules"]
    head_chars = pipeline_cfg.get("classify", {}).get("head_chars", 4000)

    def run(folder: Path):
        out = []
        for pdf in sorted(folder.rglob("*.pdf")):
            if a.positives != folder and a.positives.resolve() in pdf.resolve().parents:
                continue  # positives inside the negatives folder are not negatives
            cached = (a.ocr_cache / pdf.relative_to(folder)).with_suffix(".txt") if a.ocr_cache else None
            doc = load_document(pdf, None, cached_ocr=cached)
            s, hits = score_rules(doc, rules, head_chars)
            out.append((pdf, s, hits, doc.source))
        return out

    pos, neg = run(a.positives), run(a.negatives)
    if not pos:
        print("no positive PDFs found")
        return 2
    print(f"\nRule hit rates for '{a.type}'   (positives n={len(pos)}, negatives n={len(neg)})")
    for i, r in enumerate(rules):
        pr = sum(h[i] for _, _, h, _ in pos) / len(pos)
        nr = sum(h[i] for _, _, h, _ in neg) / max(1, len(neg))
        flag = "  <- also fires on negatives" if nr > 0.05 and r["weight"] > 0 else ""
        print(f"  {r['weight']:+5.1f}  pos {pr:5.0%}  neg {nr:5.0%}  {r['pattern'][:70]}{flag}")
    pmin = min(s for _, s, _, _ in pos)
    nmax = max((s for _, s, _, _ in neg), default=-math.inf)
    print(f"\nPositives: scores {sorted(s for _, s, _, _ in pos)}")
    print(f"Negatives: highest scores {sorted((s for _, s, _, _ in neg), reverse=True)[:10]}")
    for pdf, s, _, src in pos:
        if s <= nmax:
            print(f"  weak positive: {pdf.name} score {s} [{src}]")
    current = dt.cfg["classify"]["threshold"]
    if pmin > nmax:
        suggested = math.floor((pmin + nmax) / 2) + 1 if nmax > -math.inf else pmin
        print(f"\nGap between negatives (max {nmax}) and positives (min {pmin}).")
        print(f"Suggested threshold: {suggested}   (current: {current})")
        print(f"Margin: {pmin - suggested} above the threshold for the weakest positive, "
              f"{suggested - nmax} below it for the strongest negative.")
    else:
        print(f"\nOVERLAP: some negatives score as high as positives (max negative {nmax} >= min positive {pmin}).")
        print("Add a pattern that only positives have, or a negative-weight pattern for the confusing documents.")
        for pdf, s, _, src in neg:
            if s >= pmin:
                print(f"  strong negative: {pdf.name} score {s} [{src}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
