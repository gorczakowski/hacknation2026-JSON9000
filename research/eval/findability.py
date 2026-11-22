"""Reader quality: how many gold values can be found in a reader's cached text?

This is an upper bound on extraction recall for that reader, independent of any extractor.
Each gold leaf is searched in a type-aware way:
  digits (NIP/REGON/KRS/PESEL/accounts)  digits with optional spaces/dashes/newlines between them
  dates                                    ISO, DD.MM.YYYY, D <miesiąc> YYYY, ...
  amounts                                  10000.0 -> "10 000,00", "10000.00", ...
  other text                               normalized substring ("exact"), else fuzzy partial match >= 90 ("fuzzy")
Fields that are not text in the document (ids, enums, flags) are skipped.

Usage:
    python -m research.eval.findability --gold hacknation-2026/dane --readers pypdfium2 mineru_flash_txt mineru_flash_auto
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from rapidfuzz import fuzz

from research.eval.score import gold_path_for, subset_of

SKIP = {"czynnosc_powod_id", "czynnosc_opis", "przez-pelnomocnika", "prowadzacy_postepowanie", "typ"}
DIGIT_FIELDS = {"krs", "nip", "regon", "pesel", "na_rachunek", "z_rachunku"}
DATE_FIELDS = {"data", "czynnosc_dzien", "data_operacji"}
MONTHS = ["stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca", "sierpnia", "września",
          "października", "listopada", "grudnia"]


def norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s).casefold()
    s = re.sub(r"[„”“\"'`´]", '"', s).replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip()


def leaves(d, path=""):
    if isinstance(d, dict):
        for k, v in d.items():
            yield from leaves(v, f"{path}.{k}" if path else k)
    elif isinstance(d, list):
        for v in d:
            yield from leaves(v, path + "[]")
    else:
        yield path, d


def find(field: str, value, text: str, ntext: str) -> str:
    """Return 'exact', 'fuzzy' or 'missing'."""
    v = str(value)
    if field in DIGIT_FIELDS:
        pat = r"[\s\-]*".join(re.escape(c) for c in re.sub(r"\D", "", v))
        return "exact" if re.search(pat, text) else "missing"
    if field in DATE_FIELDS:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) or not (1 <= int(v[5:7]) <= 12):
            return "missing"
        y, m, d = v.split("-")
        cands = [v, f"{d}.{m}.{y}", f"{int(d)}.{m}.{y}", f"{d}-{m}-{y}", f"{d}/{m}/{y}",
                 f"{int(d)} {MONTHS[int(m) - 1]} {y}", f"{d} {MONTHS[int(m) - 1]} {y}"]
        return "exact" if any(norm(c) in ntext for c in cands) else "missing"
    if field == "kwota":
        whole, frac = f"{float(value):.2f}".split(".")
        groups = f"{int(whole):,}".replace(",", " ")
        cands = [f"{groups},{frac}", f"{whole},{frac}", f"{whole}.{frac}", f"{groups}.{frac}",
                 groups.replace(" ", "."), f"{groups} zł", f"{whole} zł"]
        return "exact" if any(c in ntext.replace("\xa0", " ") for c in cands) else "missing"
    if field == "numer_ted":
        num = v.split("-")[0]
        return "exact" if num in text else "missing"
    if field == "numer_bzp":
        pat = r"\s*".join(re.escape(c) for c in v.replace(" ", ""))
        return "exact" if re.search(pat, text, re.I) else "missing"
    nv = norm(v)
    if nv in ntext:
        return "exact"
    if len(nv) >= 4 and fuzz.partial_ratio(nv, ntext) >= 90:
        return "fuzzy"
    return "missing"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gold", type=Path, required=True)
    ap.add_argument("--readers", nargs="+", required=True)
    ap.add_argument("--text", type=Path, default=Path("out/text"))
    ap.add_argument("--only")
    ap.add_argument("--fields", action="store_true")
    ap.add_argument("--missing", type=int, default=0, help="print N missing examples per reader")
    args = ap.parse_args()

    pdfs = sorted(p for p in args.gold.rglob("*") if p.suffix.lower() == ".pdf")
    if args.only:
        pdfs = [p for p in pdfs if args.only in str(p.relative_to(args.gold)).replace("\\", "/")]
    for reader in args.readers:
        tot, by_set, by_field = defaultdict(int), defaultdict(lambda: defaultdict(int)), defaultdict(lambda: defaultdict(int))
        missing, no_text = [], 0
        for pdf in pdfs:
            gp = gold_path_for(pdf)
            if not gp:
                continue
            rel = pdf.relative_to(args.gold)
            tp = (args.text / reader / rel).with_suffix(".txt")
            text = tp.read_text(encoding="utf-8") if tp.exists() else ""
            if not text.strip():
                no_text += 1
            ntext = norm(text)
            gold = json.loads(gp.read_text(encoding="utf-8"))
            for path, val in leaves(gold):
                fld = path.split(".")[-1]
                if val is None or fld in SKIP:
                    continue
                r = find(fld, val, text, ntext)
                tot[r] += 1
                by_set[subset_of(rel)][r] += 1
                by_field[path][r] += 1
                if r == "missing":
                    missing.append((str(rel).replace("\\", "/"), path, val))

        def pct(c):
            n = sum(c.values()) or 1
            return f"exact {c['exact'] / n:6.1%}  +fuzzy {(c['exact'] + c['fuzzy']) / n:6.1%}  (n={n})"

        print(f"\n===== {reader}   (docs without text: {no_text})")
        print(f"ALL        {pct(tot)}")
        for s in sorted(by_set):
            print(f"  {s:8s} {pct(by_set[s])}")
        if args.fields:
            for f in sorted(by_field, key=lambda f: by_field[f]["exact"] / max(1, sum(by_field[f].values()))):
                print(f"    {f:42s} {pct(by_field[f])}")
        for m in missing[: args.missing]:
            print("    MISSING", m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
