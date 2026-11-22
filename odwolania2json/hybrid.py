"""Rules + model hybrid: when the model is asked, and how its answer is combined with the rules.

Classification ("safety net"):
  - rules are SURE  -> the rules decide, no model call. Sure = the best type reached its threshold by at least
                       `sure_margin` points AND leads every other type by at least `sure_margin` points.
  - rules see NOTHING (every type scores <= 0) -> unrelated document ({}), no model call.
  - otherwise (unsure) -> the model chooses among the types' one-sentence `classify.description`s (or "none").
    If the model is unavailable or fails, the rules' own answer is used.

Fields: per document type `llm.mode`
  - fill         rules first; the model only fills fields the rules left empty   (appeals, transfers)
                 Fields decided by a `final: true` rule are never filled by the model; when nothing else is
                 empty the model is not asked.
  - model_first  the model's answer wins; rules fill what the model left empty     (powers of attorney)
  - off          rules only
  For OCR'd scans `hybrid.scan_mode` (default model_first) overrides it, because rules are weaker on OCR text.
  Lists (e.g. attorneys) are taken whole from one source. In model_first on text-layer PDFs, an item of the model's
  list is then completed from the rules' item with the same identifier (NIP, KRS, REGON or PESEL), if exactly one
  matches. Not on OCR text, where the rules' items are too noisy.
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


ID_KEYS = ("nip", "krs", "regon", "pesel")


def _ids(item, out=None):
    """(key, value) pairs of the identifiers anywhere in a list item (e.g. data.nip), nested lists excluded."""
    out = set() if out is None else out
    if isinstance(item, dict):
        for k, v in item.items():
            if k in ID_KEYS and not _empty(v) and not isinstance(v, (dict, list)):
                out.add((k, str(v)))
            elif isinstance(v, dict):
                _ids(v, out)
    return out


def _merge_items(primary: list, secondary: list) -> list:
    """Each primary item keeps its values; its empty fields are filled from the one secondary item that shares an
    identifier with it (NIP, KRS, REGON, PESEL), if exactly one does and that item matches no other primary item."""
    ids_p = [_ids(p) for p in primary]
    ids_s = [_ids(s) for s in secondary]
    out = []
    for i, p in enumerate(primary):
        cand = [j for j, s in enumerate(ids_s) if ids_p[i] & s]
        if len(cand) == 1 and sum(1 for ip in ids_p if ip & ids_s[cand[0]]) == 1:
            p = merge(p, secondary[cand[0]], by_identity=True)
        out.append(p)
    return out


def merge(primary, secondary, by_identity=False):
    """primary's values win; secondary fills what primary left empty. Lists are taken whole from one source;
    with `by_identity`, items of the chosen list are completed from the other source's item with the same ID."""
    if isinstance(primary, dict) and isinstance(secondary, dict):
        return {k: merge(primary.get(k), secondary.get(k), by_identity) for k in primary}
    if isinstance(primary, list) or isinstance(secondary, list):
        if not primary:
            return secondary or []
        if (by_identity and isinstance(secondary, list) and secondary
                and all(isinstance(x, dict) for x in primary + secondary)):
            return _merge_items(primary, secondary)
        return primary
    return secondary if _empty(primary) else primary


def _has_gap(obj, final, path="") -> bool:
    """True if some field outside `final` is empty (a list counts as one field)."""
    if path in final:
        return False
    if isinstance(obj, dict):
        return any(_has_gap(v, final, f"{path}.{k}" if path else k) for k, v in obj.items())
    return _empty(obj)


def _set(obj, path, value):
    keys = path.split(".")
    for k in keys[:-1]:
        obj = obj[k]
    obj[keys[-1]] = value


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


def model_fallback(doc, dt, data, llm, hcfg, evidence, final=frozenset()):
    """Ask the model for the whole record and combine it with the rules' record according to the mode.

    `final`: paths decided by a `final: true` rule. In `fill` mode the model does not change them, and when every
    other field is already filled the model is not asked at all."""
    mode = (dt.cfg.get("llm") or {}).get("mode", "fill")
    if doc.source in ("ocr", "text+ocr"):
        mode = hcfg.get("scan_mode", "model_first") if mode != "off" else "off"
    if llm is None or mode == "off":
        return data, "rules"
    if mode == "fill" and not _has_gap(data, final):
        return data, "rules (complete, model not asked)"
    extra = (dt.cfg.get("llm") or {}).get("instructions", "")
    prompt = EXTRACT_PROMPT.format(doc_type=dt.name, spec=dt.template_text, extra=("\n" + extra) if extra else "")
    try:
        raw = llm.chat_json(prompt, doc.text[: int(hcfg.get("max_chars", 20000))])
    except (LLMError, ValueError) as e:
        log.warning("model extraction failed for %s: %s", doc.path.name, e)
        return data, "rules (model failed)"
    model = sanitize(conform(raw, dt.template))
    before = copy.deepcopy(data)
    if mode == "model_first":
        # completing the model's list items from the rules is only safe where the rules read a text layer
        merged = merge(model, data, by_identity=doc.source == "text")
    else:
        merged = merge(data, model)
        for p in final:
            _set(merged, p, copy.deepcopy(_get_raw(before, p)))
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


def _get_raw(obj, path):
    for k in path.split("."):
        obj = obj[k]
    return obj


def _get(obj, path):
    for k in path.replace("[]", "").split("."):
        obj = obj.get(k) if isinstance(obj, dict) else None
    return obj if not isinstance(obj, (dict, list)) else json.dumps(obj, ensure_ascii=False)[:200]
