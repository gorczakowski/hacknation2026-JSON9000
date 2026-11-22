"""Named post-extraction rules, referenced from doctype configs (`derive: [...]`)."""

from __future__ import annotations

import re

from . import values as V

# --- appeal: the passage stating the challenged action ---------------------------------------

# Defaults; every pattern can be overridden in the doctype config under `powod.passage` (see odwolanie.yaml).
DEFAULT_PASSAGE = {
    "start": [r"wnosz[ęe]\s+(odwołanie\s+)?(wobec|od|o)\b", r"odwołanie\s+od\s+czynno", r"wobec\s*:"],
    "fallback_start": [r"Zamawiający\s+w\s+dniu"],
    "end": [r"zarzut\s+nr", r"zarzucam", r"zarzuty", r"uzasadnienie", r"w\s+zwi[aą]zku\s+z\s+powy"],
    "first_item": r"\b1\s*[).]\s*(.*?)(?=(;|,)?\s*\b2\s*[).]\s|$)",
    "max_chars": 700,
}


def _any(patterns, text, pos=0):
    best = None
    for p in patterns:
        m = re.compile(p, re.I).search(text, pos)
        if m and (best is None or m.start() < best.start()):
            best = m
    return best


def action_passage(text: str, cfg: dict | None = None) -> tuple[str, int]:
    """The sentence(s) naming the challenged action: from a start phrase to the first 'Zarzut nr' etc."""
    c = {**DEFAULT_PASSAGE, **(cfg or {})}
    t = re.sub(r"\s+", " ", text)
    m = _any(c["start"], t) or _any(c["fallback_start"], t)
    if not m:
        return t[: c["max_chars"]], 0
    start = m.start()
    e = _any(c["end"], t, start + 10)
    end = e.start() if e and e.start() - start < c["max_chars"] else start + c["max_chars"]
    return t[start:end].strip(), start


def first_item(passage: str, cfg: dict | None = None) -> str:
    """In 'polegających na: 1) X; 2) Y' keep only X (the first challenged action decides the code)."""
    m = re.search((cfg or {}).get("first_item", DEFAULT_PASSAGE["first_item"]), passage)
    return m.group(1) if m else passage


def powod_and_date(data, doc, dt, evidence):
    cfg = dt.cfg["powod"]
    pcfg = cfg.get("passage")
    passage, _ = action_passage(doc.norm, pcfg)
    item = first_item(passage, pcfg)
    pid, how = None, "passage_rules"
    for rule in cfg["rules"]:
        if re.search(rule["pattern"], item, re.I):
            pid = rule["id"]
            break
    head = data["OdwolanieKIO"]
    llm = getattr(doc, "llm", None)
    if pid is None and llm is not None:  # safety net: no keyword matched -> ask the model about this passage only
        labels = "\n".join(f"{k}: {v}" for k, v in cfg["labels"].items())
        try:
            ans = llm.chat_json(
                "Którą czynność lub zaniechanie Zamawiającego kwestionuje odwołanie? Jeśli wymieniono kilka, "
                "wybierz PIERWSZĄ. Wybierz dokładnie jeden numer z listy i zwróć JSON {\"id\": <numer>}.\n" + labels,
                item[:2000])
            cand = int(ans.get("id"))
            if cand in cfg["labels"]:
                pid, how = cand, "passage_model"
        except Exception:  # noqa: BLE001 - keep the whole-document value below
            pass
    if pid is None:  # keep what the whole-document model put there (if anything valid)
        try:
            prev = int(head.get("czynnosc_powod_id")) if head.get("czynnosc_powod_id") is not None else None
        except (TypeError, ValueError):
            prev = None
        pid, how = (prev, "model_document") if prev in cfg["labels"] else (None, "none")
    head["czynnosc_powod_id"] = pid
    head["czynnosc_opis"] = cfg["labels"].get(pid) if pid else None
    dates = V.find_dates(item) or V.find_dates(passage)
    if dates:  # the date in the challenged-action passage wins over anything else
        head["czynnosc_dzien"] = dates[0][0]
    evidence.append({"field": "OdwolanieKIO.czynnosc_powod_id", "value": pid, "method": how,
                     "context": item[:200]})
    if dates:
        evidence.append({"field": "OdwolanieKIO.czynnosc_dzien", "value": head["czynnosc_dzien"],
                         "method": "passage_date", "context": item[:200]})
    return data


def przez_pelnomocnika(data, doc, dt, evidence):
    for o in data.get("Odwolujacy", []):
        o["przez-pelnomocnika"] = 1 if o["pelnomocnik"]["imie_nazwisko"] else 0
    return data


def prowadzacy_postepowanie(data, doc, dt, evidence):
    zs = data.get("Zamawiajacy", [])
    for i, z in enumerate(zs):
        z["prowadzacy_postepowanie"] = (1 if i == 0 else 0) if len(zs) > 1 else None
    return data


def mocodawca_typ(data, doc, dt, evidence):
    for m in data.get("mocodawca", []):
        d = m["data"]
        fiz = bool(d.get("imie_nazwisko")) and not d.get("krs")
        d["typ"] = "osoba_fizyczna" if fiz else "osoba_prawna"
        if not fiz:
            d["imie_nazwisko"] = None
    return data


def signatories(data, doc, dt, evidence):
    """Names printed under signature lines (dot leaders), grouped per 'W imieniu X:' block.
    Signatures after 'Pełnomocnictwo przyjmuję' belong to the attorneys and are ignored."""
    from .extract import NAME_RE, strip_title
    raw = doc.text.replace("\r\n", "\n").replace("\r", "\n")
    cut = re.search(r"pełnomocnictwo\s+przyjmuj", raw, re.I)
    raw = raw[: cut.start()] if cut else raw
    blocks = re.split(r"(?im)^\s*w\s+imieniu\s+(.+?):?\s*$", raw)
    # blocks = [before, name1, body1, name2, body2, ...]
    groups = [("", blocks[0])] if len(blocks) == 1 else list(zip(blocks[1::2], blocks[2::2]))
    names_per_group = []
    for _, body in groups:
        names = []
        for m in re.finditer(r"(?:\.{5,}|…{3,}|_{5,})[ \t]*\n\s*([^\n]+)", body):
            line = strip_title(m.group(1).strip())
            nm = NAME_RE.fullmatch(line)
            if nm:
                names.append(nm.group(1))
        names_per_group.append(names)
    moc = data.get("mocodawca", [])
    if any(mo.get("podpisujacy") for mo in moc):  # already filled (e.g. by the model): keep it
        return data
    if len(groups) > 1 and len(moc) == len(groups):
        for mo, names in zip(moc, names_per_group):
            mo["podpisujacy"] = [{"imie_nazwisko": n} for n in names]
    elif moc:
        allnames = [n for g in names_per_group for n in g]
        moc[0]["podpisujacy"] = [{"imie_nazwisko": n} for n in allnames]
        for mo in moc[1:]:
            mo["podpisujacy"] = []
    evidence.append({"field": "mocodawca[].podpisujacy", "value": names_per_group, "method": "signatories"})
    return data


# --- normalization -----------------------------------------------------------------------------

ID_KEYS = {"krs": 10, "nip": 10, "regon": None, "pesel": 11, "na_rachunek": 26, "z_rachunku": 26}


def normalize(data, doc, dt, evidence):
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("na_rachunek", "z_rachunku") and isinstance(v, str):
                    o[k] = V.norm_account(v) or None
                elif k in ID_KEYS and isinstance(v, str):
                    d = re.sub(r"\D", "", v)
                    o[k] = d or None
                # a notice number that does not parse is not one (seen: the model put the TED number in numer_bzp)
                elif k == "numer_bzp" and isinstance(v, str):
                    r = V.parse_bzp(v)
                    o[k] = r[0] if r else None
                elif k == "numer_ted" and isinstance(v, str):
                    r = V.parse_ted(v)
                    o[k] = r[0] if r else None
                elif k == "kwota" and v is not None:
                    o[k] = abs(float(v))
                elif k == "imie_nazwisko" and isinstance(v, str):
                    from .extract import strip_title
                    o[k] = strip_title(v) or None
                elif k == "mail" and isinstance(v, str):
                    o[k] = v.strip().rstrip(".")
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(data)
    return data


def header_line(data, doc, dt, evidence):
    """Header date/place when the rules (and, on scans, the model) found no date: the model picks ONE numbered line
    from the top of the document; Python parses its answer and keeps a value only if it is visible in that line.
    Asking for a line instead of a value keeps the model's reading of unknown formats ("3 IX 2026", "Miejsce i data:")
    without its typical error: a place or date taken from an address or the body when the header has none.
    Configured under `header_line:` (date / place paths, prompt, max_chars, max_lines)."""
    from .extract import fold
    cfg = dt.cfg.get("header_line") or {}
    llm = getattr(doc, "llm", None)
    dpath, ppath = cfg.get("date"), cfg.get("place")
    if llm is None or not dpath or _path_get(data, dpath) is not None:
        return data
    cands = [ln.strip()[:150] for ln in doc.norm[: int(cfg.get("max_chars", 5000))].split("\n") if ln.strip()]
    cands = cands[: int(cfg.get("max_lines", 80))]
    if not cands:
        return data
    try:
        ans = llm.chat_json(cfg["prompt"], "\n".join(f"{i + 1}: {ln}" for i, ln in enumerate(cands)))
        line = cands[int(ans.get("wiersz")) - 1]
    except Exception:  # noqa: BLE001 - no answer, no line, or a number outside the list: keep the rules' null
        return data
    iso = V.parse_date_loose(str(ans.get("data") or ""))
    if iso:  # the day and the year (4 or 2 digits) must be visible in the chosen line
        y, _, d = iso.split("-")
        if not (re.search(rf"(?<!\d)0?{int(d)}(?!\d)", line)
                and (y in line or re.search(rf"(?<!\d){y[2:]}(?!\d)", line))):
            iso = None
    if not iso:  # a header line must at least give the date; a place alone is not trusted
        return data
    _path_set(data, dpath, iso)
    evidence.append({"field": dpath, "value": iso, "method": "header_line:model", "context": line})
    place = ans.get("miejscowosc")
    if ppath and _path_get(data, ppath) is None and isinstance(place, str) and place.strip() \
            and fold(place.strip()).lower() in fold(line).lower():
        _path_set(data, ppath, place.strip())
        evidence.append({"field": ppath, "value": place.strip(), "method": "header_line:model", "context": line})
    return data


def _path_get(obj, path):
    for k in path.split("."):
        obj = obj.get(k) if isinstance(obj, dict) else None
    return obj


def _path_set(obj, path, value):
    keys = path.split(".")
    for k in keys[:-1]:
        obj = obj[k]
    obj[keys[-1]] = value


REGISTRY = {
    "header_line": header_line,
    "powod_and_date": powod_and_date,
    "przez_pelnomocnika": przez_pelnomocnika,
    "prowadzacy_postepowanie": prowadzacy_postepowanie,
    "mocodawca_typ": mocodawca_typ,
    "normalize": normalize,
    "signatories": signatories,
}
