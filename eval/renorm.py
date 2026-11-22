"""Re-apply naive.normalize to saved predictions: <dir> -> <dir>_norm."""
import json, shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "naive"))
import naive  # noqa: E402

src = Path(sys.argv[1]); dst = Path(str(src) + "_norm")
shutil.rmtree(dst, ignore_errors=True)
for f in src.rglob("*.json"):
    if "_debug" in f.parts:
        continue
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        t = "odwolanie" if "OdwolanieKIO" in d else "pelnomocnictwo" if "mocodawca" in d else "przelew" if "przelew_bankowy" in d else None
        if t:
            d = naive.normalize(t, naive.conform(d, naive.load_specs()[1][t]))
    except Exception:  # noqa: BLE001
        d = {}
    o = dst / f.relative_to(src); o.parent.mkdir(parents=True, exist_ok=True)
    o.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
