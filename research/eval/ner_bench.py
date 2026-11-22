"""Non-LLM NER test (GLiNER, ~0.2B, local CPU) on powers of attorney (pelnomocnictwo).

Two measurements:
  A) persons: does it find all people named in the gold (attorneys + signatories + natural-person principals)?
  B) roles: with role labels ("pełnomocnik", "osoba podpisująca"), are attorneys and signatories told apart?
Run with the .venv-basal interpreter (has torch):  .venv-basal/Scripts/python.exe eval/ner_bench.py
"""

from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
from pathlib import Path

from gliner import GLiNER

G = Path("hacknation-2026/dane")
TEXT_DIRS = [Path("out/text/pypdfium2"), Path("out/text/mineru_flash_auto")]
MODEL = sys.argv[1] if len(sys.argv) > 1 else "urchade/gliner_multi-v2.1"


def norm(s):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s)).strip().casefold()


def text_for(rel):
    for d in TEXT_DIRS:
        p = (d / rel).with_suffix(".txt")
        if p.exists() and len(p.read_text(encoding="utf-8").strip()) > 50:
            return p.read_text(encoding="utf-8")
    return ""


def chunks(t, n=1200):
    # GLiNER has a short context (~384 tokens): split on paragraph boundaries
    parts, cur = [], ""
    for para in re.split(r"\n", t):
        if len(cur) + len(para) > n and cur:
            parts.append(cur); cur = ""
        cur += para + "\n"
    return parts + ([cur] if cur.strip() else [])


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0
    r = tp / (tp + fn) if tp + fn else 0
    return p, r, (2 * p * r / (p + r) if p + r else 0)


def main():
    t0 = time.time()
    m = GLiNER.from_pretrained(MODEL)
    print(f"model {MODEL} loaded in {time.time() - t0:.0f}s")
    A = [0, 0, 0]
    B = {"pelnomocnik": [0, 0, 0], "podpisujacy": [0, 0, 0]}
    times = []
    for gp in sorted(G.rglob("*.json")):
        g = json.loads(gp.read_text(encoding="utf-8"))
        if "mocodawca" not in g:
            continue
        rel = gp.relative_to(G)
        pdf = [p for p in gp.parent.glob("*.pdf") if p.stem == gp.stem or (gp.stem == "pelnomocnictwo" and p.stem == "pelnomocnictwo")]
        t = text_for(rel)
        if not t:
            continue
        att = {norm(p["imie_nazwisko"]) for p in g["pelnomocnicy"] if p["imie_nazwisko"]}
        sig = {norm(s["imie_nazwisko"]) for mo in g["mocodawca"] for s in mo["podpisujacy"] if s["imie_nazwisko"]}
        nat = {norm(mo["data"]["imie_nazwisko"]) for mo in g["mocodawca"] if mo["data"]["imie_nazwisko"]}
        gold_persons = att | sig | nat
        s = time.time()
        found_person, found_att, found_sig = set(), set(), set()
        for c in chunks(t):
            for e in m.predict_entities(c, ["osoba", "firma"], threshold=0.5):
                if e["label"] == "osoba":
                    found_person.add(norm(re.sub(r"^(r\.\s?pr\.|adw\.|mec\.)\s*", "", e["text"])))
            for e in m.predict_entities(c, ["pełnomocnik", "osoba podpisująca w imieniu mocodawcy"], threshold=0.4):
                n = norm(re.sub(r"^(r\.\s?pr\.|adw\.|mec\.)\s*", "", e["text"]))
                (found_att if e["label"] == "pełnomocnik" else found_sig).add(n)
        times.append(time.time() - s)
        A[0] += len(found_person & gold_persons); A[1] += len(found_person - gold_persons); A[2] += len(gold_persons - found_person)
        for key, gold, found in (("pelnomocnik", att, found_att), ("podpisujacy", sig, found_sig)):
            B[key][0] += len(found & gold); B[key][1] += len(found - gold); B[key][2] += len(gold - found)
    n = len(times)
    print(f"{n} powers of attorney, avg {sum(times) / n:.1f}s per document (CPU)")
    print("A) any person:            P=%.3f R=%.3f F1=%.3f" % prf(*A))
    for k, v in B.items():
        print(f"B) role {k:12s}:     P=%.3f R=%.3f F1=%.3f" % prf(*v))


if __name__ == "__main__":
    main()
