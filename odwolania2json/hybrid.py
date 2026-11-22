"""Rules + model hybrid: when the model is asked, and how its answer is combined with the rules.

Classification ("safety net"):
  - rules are SURE  -> the rules decide, no model call. Sure = the best type reached its threshold by at least
                       `sure_margin` points AND leads every other type by at least `sure_margin` points.
  - rules see NOTHING (every type scores <= 0) -> unrelated document ({}), no model call.
  - otherwise (unsure) -> the model chooses among the types' one-sentence `classify.description`s (or "none").
    If the model is unavailable or fails, the rules' own answer is used.

Fields: per document type `llm.mode`
  - fill         rules first; the model only fills fields the rules left empty   (appeals, transfers)
  - model_first  the model's answer wins; rules fill what the model left empty     (powers of attorney)
  - off          rules only
  For OCR'd scans `hybrid.scan_mode` (default model_first) overrides it, because rules are weaker on OCR text.
  Lists (e.g. attorneys) are never mixed item by item: a list is taken whole from one source.
"""

from __future__ import annotations

import copy
import json
import logging

from .llm import LLMError

log = logging.getLogger(__name__)

EXTRACT_PROMPT = """Wyodrębniasz dane z dokumentu typu "{doc_type}" i zwracasz WYŁĄCZNIE JSON
o dokładnie takiej strukturze jak wzór poniżej (te same klucze, ta sama kolejność, bez dodatkowych pól).
Komentarze we wzorze opisują pola; wartości we wzorze to tylko przykłady, NIE kopiuj ich.

Zasady:
- Jeżeli danej wartości nie ma w dokumencie, podaj null. Nie zgaduj.
- Daty w formacie YYYY-MM-DD.
- Imiona i nazwiska w mianowniku, bez tytułów (r. pr., adw., mgr, dr itp.).
- NIP, REGON, KRS, PESEL: same cyfry. Numery rachunków: bez spacji, z kodem kraju (np. PL), jeśli jest w dokumencie.
- Kwoty jako liczba (float).
- Listy mogą mieć więcej niż jeden element (np. konsorcjum, kilku pełnomocników).
{extra}
Wzór:
{spec}"""

CLASSIFY_PROMPT = """Klasyfikujesz dokument. Wybierz dokładnie jeden typ z listy (podaj jego nazwę) albo "inny",
jeśli dokument nie pasuje do żadnego. Zwróć JSON: {{"typ": "<nazwa>"}}.

Typy:
{types}
- inny: żaden z powyższych (np. wyroki i postanowienia KIO, ogłoszenia o zamówieniu, pisma urzędowe, broszury)."""


def decide_type(doc, doctypes, rules_type, scores, llm, hcfg, head_chars=4000):
    """Returns (type or None, how) where how is 'rules', 'rules-none', 'model', or 'rules-fallback'."""
    margin = float(hcfg.get("sure_margin", 2))
    by = {d.name: d for d in doctypes}
    real = {k: v for k, v in scores.items() if not k.startswith("_")}
    if rules_type is not None:
        thr = by[rules_type].cfg["classify"]["threshold"]
        others = [v for k, v in real.items() if k != rules_type]
        lead = real[rules_type] - (max(others) if others else float("-inf"))
        if real[rules_type] >= thr + by[rules_type].cfg["classify"].get("sure_margin", margin) and lead >= margin:
            return rules_type, "rules"
    elif all(v <= 0 for v in real.values()) and all(d.cfg["classify"].get("rules") for d in doctypes):
        return None, "rules-none"
    if llm is None or not hcfg.get("classify_fallback", True):
        return rules_type, "rules-fallback"
    described = [d for d in doctypes if d.cfg["classify"].get("description")]
    if not described:
        return rules_type, "rules-fallback"
    types = "\n".join(f"- {d.name}: {d.cfg['classify']['description']}" for d in described)
    try:
        ans = llm.chat_json(CLASSIFY_PROMPT.format(types=types), doc.text[:head_chars] or "(pusty dokument)")
    except (LLMError, ValueError) as e:
        log.warning("model classification failed for %s: %s", doc.path.name, e)
        return rules_type, "rules-fallback"
    t = str(ans.get("typ", "")).strip()
    return (t if t in by else None), "model"


def conform(value, template):
    """Force the model's JSON into the template's shape: same keys, missing -> None, extra dropped."""
    if isinstance(template, dict):
        value = value if isinstance(value, dict) else {}
        return {k: conform(value.get(k), tv) for k, tv in template.items()}
    if isinstance(template, list):
        if not isinstance(value, list):
            return []
        return [conform(v, template[0]) for v in value if v is not None] if template else []
    return value if not isinstance(value, (dict, list)) else None


def _empty(v):
    return v is None or v == "" or v == [] or v == {}


def merge(primary, secondary):
    """primary's values win; secondary fills what primary left empty. Lists are taken whole from one source."""
    if isinstance(primary, dict) and isinstance(secondary, dict):
        return {k: merge(primary.get(k), secondary.get(k)) for k in primary}
    if isinstance(primary, list) or isinstance(secondary, list):
        return primary if primary else (secondary or [])
    return secondary if _empty(primary) else primary


def _filled_paths(before, after, path=""):
    out = []
    if isinstance(after, dict):
        for k, v in after.items():
            out += _filled_paths((before or {}).get(k) if isinstance(before, dict) else None, v, f"{path}.{k}" if path else k)
    elif isinstance(after, list):
        if after and after != before:
            out.append(path + "[]")
    elif not _empty(after) and after != before:
        out.append(path)
    return out


def model_fallback(doc, dt, data, llm, hcfg, evidence):
    """Ask the model for the whole record and combine it with the rules' record according to the mode."""
    mode = (dt.cfg.get("llm") or {}).get("mode", "fill")
    if doc.source in ("ocr", "text+ocr"):
        mode = hcfg.get("scan_mode", "model_first") if mode != "off" else "off"
    if llm is None or mode == "off":
        return data, "rules"
    extra = (dt.cfg.get("llm") or {}).get("instructions", "")
    prompt = EXTRACT_PROMPT.format(doc_type=dt.name, spec=dt.template_text, extra=("\n" + extra) if extra else "")
    try:
        raw = llm.chat_json(prompt, doc.text[: int(hcfg.get("max_chars", 20000))])
    except (LLMError, ValueError) as e:
        log.warning("model extraction failed for %s: %s", doc.path.name, e)
        return data, "rules (model failed)"
    model = sanitize(conform(raw, dt.template))
    before = copy.deepcopy(data)
    merged = merge(model, data) if mode == "model_first" else merge(data, model)
    for p in _filled_paths(before, merged):
        evidence.append({"field": p, "value": _get(merged, p), "method": f"model:{mode}"})
    return merged, f"rules+model ({mode})"


ID_LEN = {"nip": (10,), "krs": (10,), "regon": (9, 14), "pesel": (11,)}


def sanitize(obj, key=None):
    """Drop model values that cannot be right: invalid account numbers (e.g. truncated), impossible dates,
    identifiers with the wrong number of digits. Dropped values become None so the rules' value can be used."""
    import re
    from datetime import date
    from . import values as V
    if isinstance(obj, dict):
        return {k: sanitize(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v, key) for v in obj]
    if obj is None:
        return None
    if key in ("na_rachunek", "z_rachunku"):
        return V.clean_account(str(obj))
    if key in ID_LEN:
        d = re.sub(r"\D", "", str(obj))
        return d if len(d) in ID_LEN[key] else None
    if key in ("data", "czynnosc_dzien", "data_operacji"):
        try:
            return date.fromisoformat(str(obj)).isoformat()
        except ValueError:
            return None
    if key == "kwota":
        try:
            return float(str(obj).replace(" ", "").replace(",", "."))
        except ValueError:
            return None
    if isinstance(obj, str) and obj.strip().lower() in ("", "null", "none", "brak", "n/a"):
        return None
    return obj


def _get(obj, path):
    for k in path.replace("[]", "").split("."):
        obj = obj.get(k) if isinstance(obj, dict) else None
    return obj if not isinstance(obj, (dict, list)) else json.dumps(obj, ensure_ascii=False)[:200]
