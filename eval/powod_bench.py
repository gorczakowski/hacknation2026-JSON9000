"""Benchmark ways to get czynnosc_powod_id: keyword rules vs decision models (passage or full text)."""
import argparse, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import httpx2 as httpx
from dotenv import load_dotenv
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from extract.focus import action_passage, first_item, powod_by_rules

CRITERIA = {
    "1": "Odrzucenie oferty odwołującego (zamawiający odrzucił ofertę odwołującego).",
    "2": "Wybór najkorzystniejszej oferty (zamawiający wybrał / ogłosił wybór oferty).",
    "3": "Wykluczenie wykonawcy (zamawiający wykluczył odwołującego z postępowania).",
    "4": "Publikacja specyfikacji warunków zamówienia (SWZ) lub ogłoszenia o zamówieniu.",
    "5": "Modyfikacja / zmiana treści SWZ.",
    "6": "Odpowiedzi lub wyjaśnienia treści SWZ udzielone wykonawcom.",
    "7": "Zaniechanie odrzucenia oferty (zamawiający NIE odrzucił oferty, choć powinien).",
    "8": "Zaniechanie wykluczenia wykonawcy (zamawiający NIE wykluczył wykonawcy, choć powinien).",
    "9": "Unieważnienie postępowania (zamawiający unieważnił postępowanie).",
    "10": "Zaniechanie unieważnienia postępowania (zamawiający NIE unieważnił postępowania, choć powinien).",
    "11": "Opis przedmiotu zamówienia (OPZ).",
    "12": "Projektowane postanowienia umowy / wzór umowy.",
}
INSTR = "Której czynności lub zaniechania Zamawiającego dotyczy odwołanie? Jeśli wymieniono kilka, wybierz PIERWSZĄ."


def main():
    load_dotenv(".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", default=[])
    ap.add_argument("--text", default="out/text/pypdfium2")
    ap.add_argument("--full", action="store_true", help="send full text instead of the passage")
    ap.add_argument("--show-errors", action="store_true")
    a = ap.parse_args()
    G = Path("hacknation-2026/dane")
    items = []
    for g in sorted(G.rglob("*.json")):
        d = json.loads(g.read_text(encoding="utf-8"))
        if "OdwolanieKIO" not in d:
            continue
        rel = g.relative_to(G).with_suffix(".txt")
        tp = Path(a.text) / rel
        if not tp.exists() or not tp.read_text(encoding="utf-8").strip():
            continue
        t = tp.read_text(encoding="utf-8")
        items.append((str(rel), d["OdwolanieKIO"]["czynnosc_powod_id"], t, action_passage(t)))
    print(f"{len(items)} appeals with text from {a.text}")
    ok = sum(powod_by_rules(p) == gold for _, gold, _, p in items)
    print(f"{'rules (no model)':40s} acc={ok / len(items):.3f}")
    if a.show_errors:
        for r, gold, _, p in items:
            if powod_by_rules(p) != gold:
                print("   RULE MISS", r, gold, powod_by_rules(p), "|", first_item(p)[:150])
    http = httpx.Client(timeout=120, headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"]})

    def ask(model, state):
        body = {"model": model, "state": state, "questions": {"powod": {"type": "choice", "instructions": INSTR, "criteria": CRITERIA}}}
        for attempt in range(3):
            r = http.post("https://openrouter.ai/api/alpha/decisions", json=body)
            if r.status_code == 200:
                j = r.json()
                return j["answers"]["powod"].get("choice"), float(j["usage"].get("cost") or 0)
            if r.status_code < 500 and r.status_code != 429:
                return f"ERR {r.status_code} {r.text[:120]}", 0.0
            time.sleep(2 * (attempt + 1))
        return "ERR retries", 0.0

    for m in a.models:
        t0 = time.time()
        states = [t[:12000] if a.full else p for _, _, t, p in items]
        with ThreadPoolExecutor(8) as ex:
            res = list(ex.map(lambda s: ask(m, s), states))
        errs = [r for r, _ in res if isinstance(r, str) and r.startswith("ERR")]
        acc = sum(str(gold) == str(r) for (_, gold, _, _), (r, _) in zip(items, res)) / len(items)
        cost = sum(c for _, c in res)
        print(f"{m:40s} acc={acc:.3f}  cost=${cost:.4f}  {time.time() - t0:.0f}s" + (f"  errors={len(errs)} {errs[0][:100]}" if errs else ""), flush=True)


if __name__ == "__main__":
    main()
