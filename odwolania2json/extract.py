"""Config-driven rule extractors.

A field is a list of methods tried in order; the first that yields a value wins. Methods (YAML keys):

  label: [regex, ...]      value right after one of the labels (priority = list order)
      type: account|amount|date|digitsN|email|token|bzp|ted|text|line
      stop: [regex, ...]   (text) stop before these labels; text also stops at a blank line
  regex: pattern           named group (?P<v>...) or group 1; optional `type` to parse it
  find: type               first value of that type anywhere in the scope (e.g. email, bzp, ted)
  place_date: place|date   from a "Miejscowość, (dnia) <data>" line
  first_line: true         first non-empty line of the scope (e.g. company name after "Odwołujący:")
  names_after: [regex]     list of person names after a label (titles stripped), for list fields
  const: value

Every method can set `scope: <section>` (default: the current scope).
Sections are defined per doctype: {name: {start: [regex], end: [regex], max_chars: N}}.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import values as V

TITLES = r"(?:r\.\s?pr\.|radc[aąy]\s+prawn\w*|adw\.|adwokat\w*|mec\.|mgr|dr\s+hab\.|dr|prof\.|inż\.|pan[iy]?|pana)\s*"
NAME_TOKEN = r"[A-ZŁŚŻŹĆŃÓĘĄ][a-ząćęłńóśźż]+(?:-[A-ZŁŚŻŹĆŃÓĘĄ][a-ząćęłńóśźż]+)?"
NAME_RE = re.compile(rf"(?:{TITLES})*({NAME_TOKEN}(?:\s+{NAME_TOKEN}){{1,2}})")


_FOLD = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


def fold(s: str) -> str:
    """Length-preserving diacritics fold, so offsets in folded text equal offsets in the original."""
    return s.translate(_FOLD)


@dataclass
class Hit:
    value: object
    method: str
    start: int = -1
    end: int = -1
    snippet: str = ""


@dataclass
class Scope:
    text: str
    offset: int = 0  # position of this scope in the document's normalized text
    sections: dict = field(default_factory=dict)


def _label_rx(label: str) -> re.Pattern:
    return re.compile(rf"(?<![\w])(?:{fold(label)})(?![\w])\s*[:.\-–]?\s*", re.I)


def _text_until(s: str, stops: list[str], multiline: bool) -> tuple[str, int]:
    end = len(s)
    if not multiline:
        nl = s.find("\n")
        end = nl if nl >= 0 else end
    else:
        blank = re.search(r"\n\s*\n|\f", s)
        if blank:
            end = blank.start()
    for st in stops:
        m = re.search(rf"(?:^|\n|\s)(?:{fold(st)})(?!\w)", fold(s[:end]), re.I | re.M)
        if m and m.start() > 0:
            end = min(end, m.start())
    return s[:end], end


def clean_text(v: str) -> str:
    v = re.sub(r"\s*\n\s*", " ", v).strip()
    v = v.strip(" \t\"'„”“«»,;")
    v = re.sub(r"[”\"]\.?$", "", v).strip()
    return v.rstrip(".") if v.endswith((".", ".”")) and not re.search(r"\b(?:sp|j|k|o\.o|S\.A|p)\.$", v, re.I) else v


def strip_title(name: str) -> str:
    return re.sub(rf"^(?:{TITLES})+", "", name.strip(), flags=re.I).strip(" ,;.")


def extract_names(s: str, limit: int = 10) -> list[tuple[str, int, int]]:
    out = []
    for m in NAME_RE.finditer(s):
        out.append((m.group(1), m.start(1), m.end(1)))
        if len(out) >= limit:
            break
    return out


def run_method(m: dict, scope: Scope) -> Hit | None:
    s = scope.text
    if "scope" in m:
        if m["scope"] not in scope.sections:  # section not found in this document: skip, let the next method try
            return None
        sub = scope.sections[m["scope"]]
        m = {k: v for k, v in m.items() if k != "scope"}
        return run_method(m, sub)
    if "const" in m:
        return Hit(m["const"], "const")
    if "label" in m:
        kind = m.get("type", "line")
        for lab in m["label"]:
            for lm in _label_rx(lab).finditer(fold(s)):
                rest = s[lm.end():]
                if kind in ("text", "line"):
                    raw, n = _text_until(rest, m.get("stop", []), multiline=(kind == "text"))
                    v = clean_text(raw)
                    if v:
                        return Hit(v, f"label:{lab}", scope.offset + lm.end(), scope.offset + lm.end() + n, raw)
                    continue
                r = V.parse_value(kind, rest)
                if r:
                    return Hit(r[0], f"label:{lab}", scope.offset + lm.end(), scope.offset + lm.end() + r[1],
                               rest[: r[1]])
                if not m.get("all_matches", True):
                    break
        return None
    if "regex" in m:
        rx = re.compile(m["regex"], re.I | re.M | re.S if m.get("dotall") else re.I | re.M)
        for mm in rx.finditer(s):
            g = "v" if "v" in rx.groupindex else 1
            raw = mm.group(g)
            if raw is None:
                continue
            if m.get("type") and m["type"] not in ("text", "line"):
                r = V.parse_value(m["type"], raw)
                if not r:
                    continue
                v = r[0]
            else:
                v = clean_text(raw)
            return Hit(v, "regex", scope.offset + mm.start(g), scope.offset + mm.end(g), raw)
        return None
    if "nth" in m:  # {nth: 1, of: account} -> second account number in the scope
        kind, n = m["of"], m["nth"]
        if kind != "account":
            raise ValueError(f"nth supports 'account' only, got {kind}")
        found = V.find_accounts(s)
        if len(found) > n and len(found) >= m.get("min_count", 1):
            v, a, b = found[n]
            return Hit(v, f"nth:{n}:{kind}", scope.offset + a, scope.offset + b)
        return None
    if "find" in m:
        kind = m["find"]
        if kind == "email":
            mm = V.EMAIL_RE.search(s)
            return Hit(mm.group(0), "find:email", scope.offset + mm.start(), scope.offset + mm.end()) if mm else None
        if kind == "bzp":
            r = V.parse_bzp(s)
            return Hit(r[0], "find:bzp") if r else None
        if kind == "ted":
            r = V.parse_ted(s)
            return Hit(r[0], "find:ted") if r else None
        if kind == "date":
            r = V.parse_date(s, anchored=False)
            return Hit(r[0], "find:date", scope.offset + r[1], scope.offset + r[2]) if r else None
        raise ValueError(kind)
    if "place_date" in m:
        rx = re.compile(r"^\s*([A-ZŁŚŻŹĆ][\wąćęłńóśźż]+(?:[ -][A-ZŁŚŻŹĆ][\wąćęłńóśźż]+){0,2})(?:\s*,\s*(?:dnia\s+)?|\s+dnia\s+)"
                        r"(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{4}|\d{1,2}\s+[a-ząćęłńóśźż]+\s+\d{4}|\d{4}-\d{2}-\d{2})", re.M)
        for mm in rx.finditer(s[: m.get("max_chars", 6000)]):
            place, dtxt = mm.group(1), mm.group(2)
            d = V.parse_date(dtxt)
            if not d:
                continue
            if m["place_date"] == "place":
                return Hit(place, "place_date", scope.offset + mm.start(1), scope.offset + mm.end(1))
            return Hit(d[0], "place_date", scope.offset + mm.start(2), scope.offset + mm.end(2))
        return None
    if "first_line" in m:
        for line in s.split("\n"):
            line = line.strip(" :")
            if line and not re.match(m.get("skip", r"^$"), line, re.I):
                v = clean_text(re.split(m.get("cut", r"\s+ul\.|,\s*ul\.|,\s*KRS|\s+KRS:"), line)[0])
                return Hit(v, "first_line") if v else None
        return None
    if "names_after" in m:
        for lab in m["names_after"]:
            lm = re.search(rf"(?:{lab})\s*[:.]?\s*", s, re.I)
            if not lm:
                continue
            rest, _ = _text_until(s[lm.end():], m.get("stop", []), multiline=True)
            rest = rest[: m.get("max_chars", 400)]
            names = [strip_title(n) for n, _, _ in extract_names(rest)]
            if names:
                return Hit(names, f"names_after:{lab}", scope.offset + lm.end(), scope.offset + lm.end() + len(rest))
        return None
    raise ValueError(f"unknown extraction method {m}")


def run_field(methods: list[dict], scope: Scope) -> Hit | None:
    for m in methods:
        h = run_method(m, scope)
        if h is not None and h.value not in (None, "", []):
            if m.get("first") and isinstance(h.value, list):
                h.value = h.value[0]
            return h
    return None


def build_sections(text: str, defs: dict) -> dict[str, Scope]:
    """Find each section's start label; the section runs until its own end labels or the next section start."""
    starts = {}
    ftext = fold(text)  # same length as text, so offsets carry over
    for name, d in defs.items():
        for pat in d["start"]:
            m = re.search(fold(pat), ftext, re.I | re.M)
            if m and (name not in starts or m.start() < starts[name][0]):
                starts[name] = (m.start(), m.end())
    out = {}
    for name, (s0, s1) in starts.items():
        end = len(text)
        for other, (o0, _) in starts.items():
            if other != name and o0 > s0:
                end = min(end, o0)
        for pat in defs[name].get("end", []):
            m = re.compile(fold(pat), re.I | re.M).search(ftext, s1)
            if m:
                end = min(end, m.start())
        end = min(end, s1 + defs[name].get("max_chars", 100000))
        out[name] = Scope(text[s1:end], s1)
    return out
