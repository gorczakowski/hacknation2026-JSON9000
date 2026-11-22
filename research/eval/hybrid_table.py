"""Hybrid table: rules (current out/rules) + model fallback, for every model with saved outputs.

For each model it composes the model's predictions: text PDFs from <text_dir>, scans from <scan_dir> (OCR text),
then merges rules-first (strategy fill+doc) and scores. Prints one line per model.

Usage: python -m research.eval.hybrid_table
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

PY = sys.executable
# name: (size_B, text-PDF predictions dir, scan predictions dir)
MODELS = {
    "ark-qwen3.5-4b": (4.7, "out/ark/arklabsai_Qwen3_5-4B_norm", "out/ark/arklabsai_Qwen3_5-4B_norm"),
    "ark-bielik-11b-v3.0": (11.2, "out/ark/arklabsai_Bielik-11B-v3_0-Instruct_norm", "out/ark/arklabsai_Bielik-11B-v3_0-Instruct_norm"),
    "ark-bielik-11b-v3.1": (11.2, "out/ark/arklabsai_Bielik-11B-v3_1-Instruct_norm", "out/ark/arklabsai_Bielik-11B-v3_1-Instruct_norm"),
    "ministral-3b": (3.8, "out/models/mistralai_ministral-3b-2512_norm", "out/scans_ocr/mistralai_ministral-3b-2512_norm"),
    "ministral-8b": (8.9, "out/models/mistralai_ministral-8b-2512_norm", "out/scans_ocr/mistralai_ministral-8b-2512_norm"),
    "ministral-14b": (13.9, "out/models/mistralai_ministral-14b-2512_norm", "out/models/mistralai_ministral-14b-2512_norm"),
    "qwen3-vl-8b": (8.8, "out/models_ocr/qwen_qwen3-vl-8b-instruct_norm", "out/models_ocr/qwen_qwen3-vl-8b-instruct_norm"),
    "qwen3.5-9b": (9.7, "out/models_ocr/qwen_qwen3_5-9b_norm", "out/models_ocr/qwen_qwen3_5-9b_norm"),
    "gpt-oss-20b": (20.9, "out/models/openai_gpt-oss-20b_norm", "out/scans_ocr/openai_gpt-oss-20b_norm"),
    "mistral-small-24b": (24.0, "out/naive_mistral24_norm", "out/scans_ocr/mistralai_mistral-small-3_2-24b-instruct_norm"),
}


def points(b):
    for lim, pts in [(8, 20), (14, 18), (32, 12), (70, 8), (150, 4)]:
        if b <= lim:
            return pts
    return 0


def score(pred: Path) -> dict:
    out = Path("out/hyb/_s.json")
    subprocess.run([PY, "-m", "research.eval.score", "--gold", "hacknation-2026/dane", "--pred", str(pred), "--mode", "strict",
                    "--json", str(out)], check=True, capture_output=True)
    return json.loads(out.read_text())["strict"]


def fmt(name, size, s):
    t, b = s["by_subset"], s["by_type"]
    return (f"{name:28s} {size:>5}  pts={points(size) if isinstance(size, float) else 20:>2}  F1={s['f1']:.3f}  "
            f"text={t.get('tekst', 0):.3f} scans={t.get('skany', 0):.3f}  odw={b.get('odwolanie', 0):.3f} "
            f"pel={b.get('pelnomocnictwo', 0):.3f} prz={b.get('przelew', 0):.3f}")


def main():
    print(fmt("rules only", "-", score(Path("out/rules"))))
    for name, (size, tdir, sdir) in MODELS.items():
        if not Path(tdir).exists() or not Path(sdir).exists():
            print(f"{name:28s} (missing outputs)")
            continue
        comp = Path("out/hyb/_model")
        shutil.rmtree(comp, ignore_errors=True)
        shutil.copytree(tdir, comp)
        if sdir != tdir and (Path(sdir) / "skany").exists():
            shutil.rmtree(comp / "skany", ignore_errors=True)
            shutil.copytree(Path(sdir) / "skany", comp / "skany")
        print(fmt(f"{name} alone", size, score(comp)))
        subprocess.run([PY, "-m", "research.eval.merge", "--rules", "out/rules", "--model", str(comp), "--out", "out/hyb/_merged",
                        "--strategy", "fill+doc"], check=True)
        print(fmt(f"rules + {name}", size, score(Path("out/hyb/_merged"))))
        subprocess.run([PY, "-m", "research.eval.merge", "--rules", "out/rules", "--model", str(comp), "--out", "out/hyb/_merged",
                        "--strategy", "fill+doc", "--model-first", "pelnomocnictwo"], check=True)
        print(fmt(f"rules + {name} (model-first POA)", size, score(Path("out/hyb/_merged"))))


if __name__ == "__main__":
    main()
