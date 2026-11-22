"""Field-level F1 scorer, following the organizers' rules (see FAQ.md, "Scoring / F1").

For every leaf field of every document:
  TP  value present in both and matching
  FP  value only in the prediction, or present in both but wrong
  FN  value only in the gold
  TN  null / absent in both (reported, not used in F1)
Long strings (FUZZY_FIELDS) match at >= 80% similarity, everything else exactly.
List items (e.g. several pelnomocnicy) are paired by content, not by position.

Two modes bracket the unknown official normalization:
  strict   exact string equality (after trimming)
  lenient  case-insensitive, whitespace-collapsed, punctuation-normalized

Usage:
    python -m eval.score --gold hacknation-2026/dane --pred out/naive [--errors out/naive_errors.jsonl]
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

FUZZY_THRESHOLD = 80.0
# path patterns (list indices replaced by []) of the "long string" fields
FUZZY_FIELDS = {
    "Odwolujacy[].nazwa",
    "Zamawiajacy[].nazwa",
    "Odwolujacy[].pelnomocnik.nazwa",
    "ZamowieniePubliczne.tytul",
    "przelew_bankowy.tytul",
}


# --- gold discovery ------------------------------------------------------------

def gold_path_for(pdf: Path) -> Path | None:
    same = pdf.with_suffix(".json")
    if same.exists():
        return same
    if pdf.stem == "potwierdzenie_przelewu" and (pdf.parent / "przelew.json").exists():
        return pdf.parent / "przelew.json"
    return None  # unrelated document -> expected {}


def doc_type(d: dict) -> str:
    if not d:
        return "inny"
    if "OdwolanieKIO" in d:
        return "odwolanie"
    if "mocodawca" in d:
        return "pelnomocnictwo"
    if "przelew_bankowy" in d:
        return "przelew"
    return "?"


def subset_of(rel: Path) -> str:
    top = rel.parts[0]
    return {"skany": "skany", "negatywne": "negatywne"}.get(top, "tekst")


# --- comparison ----------------------------------------------------------------

def norm_lenient(v) -> str:
    s = unicodedata.normalize("NFC", str(v)).casefold()
    s = s.replace("„", '"').replace("”", '"').replace("“", '"').replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*([,.;:/()\-])\s*", r"\1", s)
    return s


ACCOUNT_FIELDS = {"przelew_bankowy.na_rachunek", "przelew_bankowy.z_rachunku"}
IGNORE_ACCOUNT_PREFIX = True  # training labels drop "PL"; the test set keeps it (FAQ, 11-21 21:17)


def values_match(path: str, g, p, mode: str) -> bool:
    if path in ACCOUNT_FIELDS and IGNORE_ACCOUNT_PREFIX and isinstance(g, str) and isinstance(p, str):
        g, p = re.sub(r"^[A-Z]{2}", "", g.strip()), re.sub(r"^[A-Z]{2}", "", p.strip())
    if isinstance(g, bool) or isinstance(p, bool):
        return g == p
    if isinstance(g, (int, float)) and isinstance(p, (int, float)):
        return abs(float(g) - float(p)) < 1e-6
    if isinstance(g, (int, float)) or isinstance(p, (int, float)):
        # e.g. pesel given as number vs string
        g, p = str(g), str(p)
    gs, ps = (str(g).strip(), str(p).strip()) if mode == "strict" else (norm_lenient(g), norm_lenient(p))
    if path in FUZZY_FIELDS:
        return fuzz.ratio(gs, ps) >= FUZZY_THRESHOLD
    return gs == ps


@dataclass
class Tally:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    def add(self, o: str):
        setattr(self, o, getattr(self, o) + 1)

    def __iadd__(self, other: "Tally"):
        self.tp += other.tp; self.fp += other.fp; self.fn += other.fn; self.tn += other.tn
        return self

    @property
    def p(self):
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def r(self):
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self):
        return 2 * self.p * self.r / (self.p + self.r) if self.p + self.r else 0.0


@dataclass
class DocResult:
    outcomes: list = field(default_factory=list)  # (path_pattern, outcome, gold, pred)

    def tally(self) -> Tally:
        t = Tally()
        for _, o, _, _ in self.outcomes:
            t.add(o)
        return t


def is_empty(v) -> bool:
    return v is None or v == "" or v == [] or v == {}


def leaf_outcome(path, g, p, mode):
    if is_empty(g) and is_empty(p):
        return "tn"
    if is_empty(g):
        return "fp"
    if is_empty(p):
        return "fn"
    return "tp" if values_match(path, g, p, mode) else "fp"


def compare(g, p, path: str, mode: str, out: list):
    """Recursively compare gold `g` and prediction `p`, appending leaf outcomes to `out`."""
    if isinstance(g, dict) or isinstance(p, dict):
        gd = g if isinstance(g, dict) else {}
        pd = p if isinstance(p, dict) else {}
        for k in list(gd) + [k for k in pd if k not in gd]:
            compare(gd.get(k), pd.get(k), f"{path}.{k}" if path else k, mode, out)
        return
    if isinstance(g, list) or isinstance(p, list):
        gl = g if isinstance(g, list) else []
        pl = p if isinstance(p, list) else []
        lp = path + "[]"
        for gi, pi in match_lists(gl, pl, lp, mode):
            compare(gi, pi, lp, mode, out)
        return
    out.append((path, leaf_outcome(path, g, p, mode), g, p))


def item_score(g, p, path, mode) -> float:
    o: list = []
    compare(g, p, path, mode, o)
    t = Counter(x[1] for x in o)
    return t["tp"] - 0.01 * (t["fp"] + t["fn"])


def match_lists(gl: list, pl: list, path: str, mode: str):
    """Pair gold and predicted items by content (best total score). Unpaired items pair with None."""
    n = max(len(gl), len(pl))
    if n == 0:
        return []
    gpad = gl + [None] * (n - len(gl))
    ppad = pl + [None] * (n - len(pl))
    if n <= 6:
        best, best_perm = None, None
        scores = [[item_score(gi, pj, path, mode) if gi is not None and pj is not None else 0.0
                   for pj in ppad] for gi in gpad]
        for perm in itertools.permutations(range(n)):
            s = sum(scores[i][perm[i]] for i in range(n))
            if best is None or s > best:
                best, best_perm = s, perm
        return [(gpad[i], ppad[best_perm[i]]) for i in range(n)]
    return list(zip(gpad, ppad))  # long lists: positional fallback


def score_doc(gold: dict, pred: dict, mode: str) -> DocResult:
    r = DocResult()
    compare(gold, pred, "", mode, r.outcomes)
    return r


# --- runner ----------------------------------------------------------------------

def load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8")), None
    except Exception as e:  # noqa: BLE001
        return {}, str(e)


def fmt(t: Tally) -> str:
    return f"F1={t.f1:6.3f}  P={t.p:6.3f}  R={t.r:6.3f}  (tp={t.tp} fp={t.fp} fn={t.fn} tn={t.tn})"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gold", type=Path, required=True, help="input data root (PDFs + gold JSONs)")
    ap.add_argument("--pred", type=Path, required=True, help="prediction root mirroring the input layout")
    ap.add_argument("--mode", choices=["strict", "lenient", "both"], default="both")
    ap.add_argument("--only", help="only score PDFs whose relative path contains this string")
    ap.add_argument("--errors", type=Path, help="write every FP/FN to this JSONL file")
    ap.add_argument("--fields", action="store_true", help="print per-field table")
    ap.add_argument("--json", type=Path, help="write summary JSON here")
    ap.add_argument("--account-prefix", choices=["ignore", "strict"], default="ignore",
                    help="compare account numbers with/without the country code (training gold lacks it)")
    args = ap.parse_args()

    global IGNORE_ACCOUNT_PREFIX
    IGNORE_ACCOUNT_PREFIX = args.account_prefix == "ignore"
    pdfs = sorted(p for p in args.gold.rglob("*") if p.suffix.lower() == ".pdf")
    if args.only:
        pdfs = [p for p in pdfs if args.only in str(p.relative_to(args.gold)).replace("\\", "/")]
    modes = ["strict", "lenient"] if args.mode == "both" else [args.mode]
    summary = {}
    missing, broken = [], []
    for mode in modes:
        total, by_type, by_subset, by_field = Tally(), defaultdict(Tally), defaultdict(Tally), defaultdict(Tally)
        confusion = Counter()
        errors = []
        for pdf in pdfs:
            rel = pdf.relative_to(args.gold)
            gp = gold_path_for(pdf)
            gold = load_json(gp)[0] if gp else {}
            pp = (args.pred / rel).with_suffix(".json")
            if pp.exists():
                pred, err = load_json(pp)
                if err and mode == modes[0]:
                    broken.append(str(rel))
            else:
                pred = {}
                if mode == modes[0]:
                    missing.append(str(rel))
            gt, pt = doc_type(gold), doc_type(pred) if isinstance(pred, dict) else "?"
            confusion[(gt, pt)] += 1
            res = score_doc(gold, pred if isinstance(pred, dict) else {}, mode)
            t = res.tally()
            total += t
            by_type[gt] += t
            by_subset[subset_of(rel)] += t
            for path, o, g, p in res.outcomes:
                by_field[path].add(o)
                if o in ("fp", "fn"):
                    errors.append({"doc": str(rel).replace("\\", "/"), "field": path, "outcome": o,
                                   "gold": g, "pred": p})

        print(f"\n===== {mode.upper()}  ({len(pdfs)} PDFs)")
        print(f"OVERALL   {fmt(total)}")
        for k in sorted(by_type):
            print(f"  type {k:15s} {fmt(by_type[k])}")
        for k in sorted(by_subset):
            print(f"  set  {k:15s} {fmt(by_subset[k])}")
        if mode == modes[0]:
            types = ["odwolanie", "pelnomocnictwo", "przelew", "inny", "?"]
            print("\nclassification (rows=gold, cols=pred):")
            print(" " * 16 + "".join(f"{t[:12]:>14s}" for t in types))
            for g in types[:4]:
                print(f"{g:16s}" + "".join(f"{confusion[(g, p)]:14d}" for p in types))
        if args.fields:
            print(f"\nper field ({mode}):")
            for k in sorted(by_field, key=lambda k: by_field[k].f1):
                print(f"  {k:42s} {fmt(by_field[k])}")
        summary[mode] = {"f1": total.f1, "p": total.p, "r": total.r,
                         "by_type": {k: v.f1 for k, v in by_type.items()},
                         "by_subset": {k: v.f1 for k, v in by_subset.items()},
                         "by_field": {k: v.f1 for k, v in by_field.items()}}
        if args.errors and mode == modes[-1]:
            args.errors.parent.mkdir(parents=True, exist_ok=True)
            with args.errors.open("w", encoding="utf-8") as f:
                for e in errors:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
            print(f"\n{len(errors)} errors ({mode}) -> {args.errors}")

    if missing:
        print(f"\nWARNING: {len(missing)} predictions missing (scored as {{}}), e.g. {missing[:3]}")
    if broken:
        print(f"WARNING: {len(broken)} predictions are not valid JSON, e.g. {broken[:3]}")
    if args.json:
        args.json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
