"""Offline grounding filter: null every predicted value that cannot be found in the document's text.

Uses the same type-aware search as eval.findability (digits across spaces, date formats, amounts, fuzzy text).
Text comes from the reader cache (text layer, else OCR cache).

Usage: python -m research.eval.ground --pred out/naive_mistral24_norm --out out/naive_mistral24_grounded
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from research.eval.findability import SKIP, find, norm

TEXT_DIRS = [Path("out/text/pypdfium2"), Path("out/text/mineru_flash_auto")]


def doc_text(rel: Path) -> str:
    for d in TEXT_DIRS:
        p = (d / rel).with_suffix(".txt")
        if p.exists():
            t = p.read_text(encoding="utf-8")
            if t.strip():
                return t
    return ""


def ground(obj, text, ntext, dropped, path=""):
    if isinstance(obj, dict):
        return {k: ground(v, text, ntext, dropped, f"{path}.{k}" if path else k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [ground(v, text, ntext, dropped, path + "[]") for v in obj]
    fld = path.split(".")[-1]
    if obj is None or fld in SKIP or not text:
        return obj
    if find(fld, obj, text, ntext) == "missing":
        dropped.append((path, obj))
        return None
    return obj


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    shutil.rmtree(a.out, ignore_errors=True)
    n_drop = 0
    for f in a.pred.rglob("*.json"):
        if "_debug" in f.parts:
            continue
        rel = f.relative_to(a.pred)
        d = json.loads(f.read_text(encoding="utf-8"))
        text = doc_text(rel)
        dropped = []
        d = ground(d, text, norm(text), dropped)
        n_drop += len(dropped)
        o = a.out / rel
        o.parent.mkdir(parents=True, exist_ok=True)
        o.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"dropped {n_drop} ungrounded values")


if __name__ == "__main__":
    main()
