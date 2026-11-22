"""Naive "System One" baseline: decision model classifies, Clairic-style extraction model extracts.

PDF -> text layer (or page images for scans)
    -> Decisions API (choice question)         -> document type
    -> Extractions API (typed fields, spans)   -> values
    -> Decisions API (e.g. czynnosc_powod_id)  -> enum fields
    -> derive rules + normalization            -> JSON in the exact spec shape

Field mapping lives in research/naive/systemone_fields.yaml.

Usage:
    python research/naive/systemone.py --input hacknation-2026/dane --output out/s1 [--limit 5]
Env (.env): OPENROUTER_API_KEY, S1_DECISION_MODEL (default liquid/d1.2), S1_EXTRACT_MODEL (default cloudflare/clairic)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx2 as httpx
import yaml
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from naive import DOC_TYPES, POWOD, ROOT, load_specs, normalize, read_pdf  # noqa: E402

API = "https://openrouter.ai/api/alpha"
CONFIG = Path(__file__).with_name("systemone_fields.yaml")
CLASSIFY_MAX_CHARS = 6000


class SystemOne:
    def __init__(self, decision_model: str, extract_model: str):
        self.decision_model = decision_model
        self.extract_model = extract_model
        self.http = httpx.Client(timeout=180, headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"]})
        self.lock = threading.Lock()
        self.cost = 0.0
        self.calls = 0

    def _post(self, endpoint: str, body: dict) -> dict:
        last = None
        for attempt in range(4):
            try:
                r = self.http.post(f"{API}/{endpoint}", json=body)
                if r.status_code >= 500 or r.status_code == 429:
                    raise RuntimeError(f"{r.status_code}: {r.text[:300]}")
                if r.status_code != 200:
                    raise ValueError(f"{r.status_code}: {r.text[:500]}")  # not retried
                j = r.json()
                with self.lock:
                    self.calls += 1
                    self.cost += float((j.get("usage") or {}).get("cost") or 0)
                return j
            except ValueError:
                raise
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(3 * (attempt + 1))
        raise RuntimeError(f"{endpoint} failed: {last}")

    @staticmethod
    def _state(text: str, images: list[str], max_chars: int):
        if images:
            return [{"type": "image_url", "image_url": {"url": u}} for u in images[:4]]
        return text[:max_chars]

    def decide(self, text, images, questions: dict, max_chars: int) -> dict:
        body = {"model": self.decision_model, "state": self._state(text, images, max_chars), "questions": questions}
        return self._post("decisions", body)["answers"]

    def extract(self, text, images, fields: dict) -> dict:
        body = {"model": self.extract_model, "fields": fields}
        if images:
            body["assets"] = {
                f"page{i + 1}": {"data_base64": u.split(",", 1)[1], "format": "jpeg"} for i, u in enumerate(images[:4])
            }
        else:
            body["state"] = text
        return self._post("extractions", body)["answers"]


# --- output assembly ---------------------------------------------------------------

def build(template, flat: dict, prefix: str = ""):
    """Build the output from the spec template, filling leaves from flat path->value."""
    if isinstance(template, dict):
        return {k: build(v, flat, f"{prefix}.{k}" if prefix else k) for k, v in template.items()}
    if isinstance(template, list):
        idx = sorted({int(m.group(1)) for key in flat
                      for m in [re.match(re.escape(prefix) + r"\[(\d+)\]", key)] if m})
        items = [build(template[0], flat, f"{prefix}[{i}]") for i in idx]
        return [it for it in items if has_value(it)]
    return flat.get(prefix)


def has_value(x) -> bool:
    if isinstance(x, dict):
        return any(has_value(v) for v in x.values())
    if isinstance(x, list):
        return any(has_value(v) for v in x)
    return x is not None


def derive(doc_type: str, data: dict, rules: list[str]) -> dict:
    if doc_type == "odwolanie":
        if "przez_pelnomocnika" in rules:
            for o in data["Odwolujacy"]:
                o["przez-pelnomocnika"] = 1 if o["pelnomocnik"]["imie_nazwisko"] else 0
        if "prowadzacy_postepowanie" in rules:
            zs = data["Zamawiajacy"]
            for i, z in enumerate(zs):
                z["prowadzacy_postepowanie"] = (1 if i == 0 else 0) if len(zs) > 1 else None
    if doc_type == "pelnomocnictwo" and "mocodawca_typ" in rules:
        for m in data["mocodawca"]:
            d = m["data"]
            fiz = bool(d["imie_nazwisko"]) and not d["krs"]
            d["typ"] = "osoba_fizyczna" if fiz else "osoba_prawna"
            if not fiz:
                d["imie_nazwisko"] = None
    return data  # czynnosc_opis is filled by normalize()


# --- pipeline ------------------------------------------------------------------------

def process(pdf_path: Path, in_root: Path, out_root: Path, s1: SystemOne, cfg: dict, skeletons: dict) -> str:
    rel = pdf_path.relative_to(in_root)
    out_path = (out_root / rel).with_suffix(".json")
    dbg_path = (out_root / "_debug" / rel).with_suffix(".json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dbg_path.parent.mkdir(parents=True, exist_ok=True)

    text, images = read_pdf(pdf_path)
    source = "image" if images else "text"
    c = cfg["classify"]
    ans = s1.decide(text, images[:2], {"typ": {"type": "choice", "instructions": c["instructions"],
                                                "criteria": c["criteria"]}}, CLASSIFY_MAX_CHARS)["typ"]
    doc_type = ans.get("choice")
    debug = {"source": source, "type": doc_type, "type_probs": ans.get("probabilities")}

    if doc_type not in DOC_TYPES:
        result = {}
    else:
        dt = cfg["doctypes"][doc_type]
        fields, paths = {}, {}
        for name, spec in dt.get("extract", {}).items():
            spec = dict(spec)
            paths[name] = spec.pop("path")
            fields[name] = spec
        answers = s1.extract(text, images, fields)
        flat = {}
        for name, a in answers.items():
            path, val = paths[name], a.get("value")
            if a.get("type") == "list" and "[]" in path:
                for j, v in enumerate(val or []):
                    flat[path.replace("[]", f"[{j}]", 1)] = v
            else:
                flat[path] = val
        decisions = {}
        for name, q in dt.get("decide", {}).items():
            qa = s1.decide(text, images, {name: {"type": q["type"], "instructions": q["instructions"],
                                                 "criteria": q["criteria"]}}, q.get("max_chars", 15000))[name]
            v = qa.get("choice") if q["type"] == "choice" else qa.get("noul")
            if q.get("cast") == "int" and v is not None:
                v = int(v)
            flat[q["path"]] = v
            decisions[name] = qa
        debug.update({"extract": answers, "decide": decisions})
        result = build(skeletons[doc_type], flat)
        result = normalize(doc_type, derive(doc_type, result, dt.get("derive", [])))

    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    dbg_path.write_text(json.dumps(debug, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"{rel}  [{source}] -> {doc_type}"


def main() -> int:
    load_dotenv(ROOT / "research" / ".env")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", help="only PDFs whose relative path contains this string")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY missing in .env", file=sys.stderr)
        return 2
    dm = os.environ.get("S1_DECISION_MODEL", "liquid/d1.2")
    em = os.environ.get("S1_EXTRACT_MODEL", "cloudflare/clairic")

    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    _, skeletons = load_specs()
    in_root, out_root = args.input.resolve(), args.output.resolve()
    pdfs = sorted(p for p in in_root.rglob("*") if p.suffix.lower() == ".pdf")
    if args.only:
        pdfs = [p for p in pdfs if args.only in str(p.relative_to(in_root)).replace("\\", "/")]
    if args.limit:
        pdfs = pdfs[: args.limit]
    print(f"{len(pdfs)} PDFs | decision={dm} extract={em}")
    s1 = SystemOne(dm, em)
    t0, failed = time.time(), 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(process, p, in_root, out_root, s1, cfg, skeletons): p for p in pdfs}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                print(f"[{i}/{len(pdfs)}] {f.result()}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"[{i}/{len(pdfs)}] FAILED {futs[f].relative_to(in_root)}: {e}", file=sys.stderr)
    print(f"done in {time.time() - t0:.0f}s, failed={failed}, calls={s1.calls}, cost=${s1.cost:.4f}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
