"""Offline hybrid simulation: combine rules predictions with a model's predictions, then score.

Strategies:
  fill      rules value if present, else model value (per leaf; lists: rules list unless empty)
  doc       rules output, but the model's whole document when rules produced {} or the doc is a scan
  fill+doc  both

Usage: python -m research.eval.merge --rules out/rules --model out/naive_mistral24_norm --out out/hyb_x --strategy fill
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def empty(v) -> bool:
    return v is None or v == "" or v == [] or v == {}


def fill(r, m):
    if isinstance(r, dict) and isinstance(m, dict):
        return {k: fill(r.get(k), m.get(k)) for k in (list(r) or list(m))}
    if isinstance(r, list) and isinstance(m, list):
        # Never mix list items from two sources by position (could attach a PESEL to the wrong person):
        # keep the preferred source's list whole, fall back to the other only if it is empty.
        return r if r else m
    return m if empty(r) else r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", type=Path, required=True)
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--strategy", choices=["fill", "doc", "fill+doc"], default="fill+doc")
    ap.add_argument("--model-first", nargs="*", default=[], help="doc types where the model wins and rules only fill gaps")
    a = ap.parse_args()
    shutil.rmtree(a.out, ignore_errors=True)
    for rf in a.rules.rglob("*.json"):
        rel = rf.relative_to(a.rules)
        r = json.loads(rf.read_text(encoding="utf-8"))
        mf = a.model / rel
        m = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {}
        scan = rel.parts[0] == "skany"
        if "doc" in a.strategy and (not r or scan) and m:
            res = m
        elif "fill" in a.strategy and r and m and set(r) == set(m):
            dtype = "odwolanie" if "OdwolanieKIO" in r else "pelnomocnictwo" if "mocodawca" in r else "przelew"
            res = fill(m, r) if dtype in a.model_first else fill(r, m)
        else:
            res = r
        o = a.out / rel
        o.parent.mkdir(parents=True, exist_ok=True)
        o.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
