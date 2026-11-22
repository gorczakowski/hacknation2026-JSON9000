"""Decision-model benchmark: document classification (226 PDFs) and czynnosc_powod_id (70 appeals).

Works with any server speaking the Decisions / System One request shape
({"model", "state", "questions": {name: {"type": "choice", "instructions", "criteria"}}}):
  - OpenRouter:  --base-url https://openrouter.ai/api/alpha --path decisions   (key: OPENROUTER_API_KEY)
  - basal-serve: --base-url http://localhost:8000/v1 --path systemone          (no key)

Text per PDF comes from the reader cache (text layer, else MinerU OCR). Results are appended to
out/decisions/results.jsonl and printed as a table.

Usage:
  python -m research.eval.decision_bench --task both --models liquid/d1.2 baseten/joey-1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx2 as httpx
import yaml
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.eval.powod_bench import CRITERIA as POWOD_CRITERIA, INSTR as POWOD_INSTR  # noqa: E402
from research.eval.score import doc_type, gold_path_for  # noqa: E402
from odwolania2json.derive import action_passage  # noqa: E402

G = Path("hacknation-2026/dane")
TEXT_DIRS = [Path("out/text/pypdfium2"), Path("out/text/mineru_flash_auto")]
CLS = yaml.safe_load(Path("research/naive/systemone_fields.yaml").read_text(encoding="utf-8"))["classify"]


def text_for(rel: Path) -> str:
    for d in TEXT_DIRS:
        p = (d / rel).with_suffix(".txt")
        if p.exists():
            t = p.read_text(encoding="utf-8")
            if len(t.strip()) > 50:
                return t
    return ""


def load_items(task: str, state_chars: int):
    items = []
    for pdf in sorted(G.rglob("*.pdf")):
        rel = pdf.relative_to(G)
        gp = gold_path_for(pdf)
        gold = json.loads(gp.read_text(encoding="utf-8")) if gp else {}
        t = text_for(rel)
        if task == "classify":
            items.append((str(rel), doc_type(gold), t[:state_chars] if t else "(brak tekstu)"))
        elif "OdwolanieKIO" in gold and t:
            passage, _ = action_passage(t)  # odwolania2json.derive version returns (passage, start)
            items.append((str(rel), str(gold["OdwolanieKIO"]["czynnosc_powod_id"]), passage))
    return items


def main():
    load_dotenv("research/.env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", choices=["classify", "powod", "both"], default="both")
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--base-url", default="https://openrouter.ai/api/alpha")
    ap.add_argument("--path", default="decisions")
    ap.add_argument("--key-env", default="OPENROUTER_API_KEY")
    ap.add_argument("--state-chars", type=int, default=3000)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tag", default="", help="label stored with the results (e.g. 'local-cpu')")
    a = ap.parse_args()
    headers = {"Authorization": "Bearer " + os.environ[a.key_env]} if os.environ.get(a.key_env) else {}
    http = httpx.Client(timeout=600, headers=headers)
    out = Path("out/decisions"); out.mkdir(parents=True, exist_ok=True)

    tasks = ["classify", "powod"] if a.task == "both" else [a.task]
    for task in tasks:
        items = load_items(task, a.state_chars)
        if a.limit:
            items = items[: a.limit]
        q = ({"typ": {"type": "choice", "instructions": CLS["instructions"], "criteria": CLS["criteria"]}}
             if task == "classify" else
             {"typ": {"type": "choice", "instructions": POWOD_INSTR, "criteria": POWOD_CRITERIA}})
        for m in a.models:
            def ask(item):
                body = {"model": m, "state": item[2], "questions": q}
                for attempt in range(3):
                    t0 = time.time()
                    try:
                        r = http.post(f"{a.base_url}/{a.path}", json=body)
                    except Exception as e:  # noqa: BLE001
                        err = str(e)[:100]
                        time.sleep(2)
                        continue
                    if r.status_code == 200:
                        j = r.json()
                        ans = j["answers"]["typ"]
                        return ans.get("choice"), float((j.get("usage") or {}).get("cost") or 0), time.time() - t0
                    err = f"{r.status_code} {r.text[:120]}"
                    if r.status_code < 500 and r.status_code != 429:
                        break
                    time.sleep(2 * (attempt + 1))
                return f"ERR {err}", 0.0, 0.0

            t0 = time.time()
            with ThreadPoolExecutor(a.workers) as ex:
                res = list(ex.map(ask, items))
            wall = time.time() - t0
            errs = [r for r, _, _ in res if isinstance(r, str) and r.startswith("ERR")]
            correct = sum(str(r) == str(gold) for (_, gold, _), (r, _, _) in zip(items, res))
            acc = correct / len(items)
            cost = sum(c for _, c, _ in res)
            lat = sorted(t for _, _, t in res if t)
            p50 = lat[len(lat) // 2] if lat else 0
            wrong = [(rel, gold, r) for (rel, gold, _), (r, _, _) in zip(items, res) if str(r) != str(gold)][:6]
            rec = {"task": task, "model": m, "tag": a.tag, "n": len(items), "acc": round(acc, 4), "cost": round(cost, 5),
                   "wall_s": round(wall, 1), "p50_s": round(p50, 2), "errors": len(errs),
                   "state_chars": a.state_chars if task == "classify" else "passage"}
            with (out / "results.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"{task:8s} {m:40s} acc={acc:.3f} ({correct}/{len(items)}) cost=${cost:.4f} wall={wall:.0f}s "
                  f"p50={p50:.1f}s errors={len(errs)}" + (f" e.g. {errs[0][:80]}" if errs else ""), flush=True)
            if wrong and acc < 1:
                print("          wrong:", [(w[0][-30:], w[1], w[2]) for w in wrong[:3]], flush=True)


if __name__ == "__main__":
    main()
