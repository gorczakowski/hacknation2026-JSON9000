"""Typed value parsers. Each parser takes text starting right after a label and returns (value, length) or None.

Types: account, amount, date, digits10/9/11/14 (via `digits`), email, token, text, bzp, ted.
"""

from __future__ import annotations

import re
from datetime import date

MONTHS = {
    "stycznia": 1, "styczeń": 1, "lutego": 2, "luty": 2, "marca": 3, "marzec": 3, "kwietnia": 4, "kwiecień": 4,
    "maja": 5, "maj": 5, "czerwca": 6, "czerwiec": 6, "lipca": 7, "lipiec": 7, "sierpnia": 8, "sierpień": 8,
    "września": 9, "wrzesień": 9, "października": 10, "październik": 10, "listopada": 11, "listopad": 11,
    "grudnia": 12, "grudzień": 12,
}
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))

DATE_RES = [
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})"), ("y", "m", "d")),
    (re.compile(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})"), ("d", "m", "y")),
    (re.compile(rf"(\d{{1,2}})\s+({_MONTH_RE})\s+(\d{{4}})", re.I), ("d", "mon", "y")),
]


def parse_date(s: str, anchored: bool = True):
    """Find a date at the start of `s` (anchored) or anywhere. Returns (iso, start, end) or None."""
    best = None
    for rx, order in DATE_RES:
        m = rx.match(s) if anchored else rx.search(s)
        if not m:
            continue
        parts = dict(zip(order, m.groups()))
        try:
            month = MONTHS[parts["mon"].lower()] if "mon" in parts else int(parts["m"])
            iso = date(int(parts["y"]), month, int(parts["d"])).isoformat()
        except (ValueError, KeyError):
            continue
        if best is None or m.start() < best[1]:
            best = (iso, m.start(), m.end())
    return best


def find_dates(s: str):
    """All dates in s, in order: list of (iso, start, end)."""
    out = []
    for rx, order in DATE_RES:
        for m in rx.finditer(s):
            parts = dict(zip(order, m.groups()))
            try:
                month = MONTHS[parts["mon"].lower()] if "mon" in parts else int(parts["m"])
                out.append((date(int(parts["y"]), month, int(parts["d"])).isoformat(), m.start(), m.end()))
            except (ValueError, KeyError):
                pass
    out.sort(key=lambda x: x[1])
    # drop overlaps (e.g. ISO inside a longer match)
    res, last_end = [], -1
    for d in out:
        if d[1] >= last_end:
            res.append(d)
            last_end = d[2]
    return res


# Bank accounts. Either an IBAN with a country code (any country: 2 letters, 2 check digits, then 11-30
# letters/digits, usually in groups of 4), or a Polish NRB without the code (26 digits). Spaces, dashes and line
# breaks between groups are allowed. Lengths are checked against IBAN_LENGTHS where the country is known.
IBAN_RE = r"[A-Z]{2}[ \u00a0]?\d{2}(?:[ \u00a0\n-]?[A-Z0-9]{4}){2,7}(?:[ \u00a0\n-]?[A-Z0-9]{1,3})?"
NRB_RE = r"\d{2}(?:[ \u00a0\n-]?\d{4}){6}"
ACCOUNT_RE = re.compile(rf"({IBAN_RE}|{NRB_RE})(?![A-Z0-9])")
IBAN_LENGTHS = {"PL": 28, "DE": 22, "AT": 20, "CZ": 24, "SK": 24, "LT": 20, "LV": 21, "EE": 20, "FR": 27, "IT": 27,
                "ES": 24, "NL": 18, "BE": 16, "LU": 20, "IE": 22, "GB": 22, "CH": 21, "DK": 18, "SE": 24, "FI": 18,
                "NO": 15, "PT": 25, "HU": 28, "RO": 24, "BG": 22, "HR": 21, "SI": 19, "GR": 27, "CY": 28, "MT": 31,
                "UA": 29}
AMOUNT_RE = re.compile(r"-?\s?(\d{1,3}(?:[  .]\d{3})+|\d+)(?:[,.](\d{2}))?(?!\d)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
BZP_RE = re.compile(r"(\d{4})\s*/\s*BZP\s*(\d{6,9})(?:\s*/\s*(\d{2}))?", re.I)
TED_RE = re.compile(r"(\d{4})\s*/\s*S\s*(\d{1,3})\s*-\s*(\d{5,7})|(\d{5,7})\s*-\s*(\d{4})")
TOKEN_RE = re.compile(r"[A-Z0-9][A-Za-z0-9./_\-]{3,}")


def norm_account(raw: str) -> str:
    """Organizer rule (11-21): no whitespace; keep the country code (e.g. PL) if the document has it."""
    return re.sub(r"[^A-Z0-9]", "", raw.upper())


def clean_account(raw: str) -> str | None:
    """Turn a regex match into an account number, or None if it isn't a plausible one.

    With a country code: the code must be a known IBAN country and the number must have at least that
    country's IBAN length (anything longer is a neighbouring word glued on, e.g. the 'K' of 'Kwota' on the next
    line, and is cut off). Without a code: exactly 26 digits (Polish NRB).
    """
    acc = norm_account(raw)
    if acc[:2].isalpha():
        n = IBAN_LENGTHS.get(acc[:2])
        if n is None or len(acc) < n:
            return None
        return acc[:n]
    digits = re.match(r"\d+", acc)
    return digits.group(0) if digits and len(digits.group(0)) == 26 else None


def find_accounts(s: str) -> list[tuple[str, int, int]]:
    """All plausible account numbers in s, in order: (value, start, end)."""
    out = []
    for m in ACCOUNT_RE.finditer(s):
        v = clean_account(m.group(1))
        if v:
            out.append((v, m.start(), m.end()))
    return out


def parse_account(s: str):
    m = ACCOUNT_RE.match(s)
    if not m:
        return None
    acc = clean_account(m.group(1))
    return (acc, m.end()) if acc else None


def parse_amount(s: str):
    m = AMOUNT_RE.match(s)
    if not m:
        return None
    whole = re.sub(r"[  .]", "", m.group(1))
    val = float(f"{whole}.{m.group(2) or '00'}")
    return val, m.end()


def parse_digits(s: str, n: int):
    m = re.match(rf"(\d[\d \-]{{{n - 2},{n + 4}}}\d)", s)
    if not m:
        return None
    d = re.sub(r"\D", "", m.group(1))
    if len(d) < n:
        return None
    return d[:n], m.end()


def parse_email(s: str):
    m = EMAIL_RE.match(s)
    return (m.group(0), m.end()) if m else None


def parse_token(s: str):
    m = TOKEN_RE.match(s)
    if not m:
        return None
    v = m.group(0).rstrip(".,;")
    return v, m.start() + len(v)


def parse_bzp(s: str, anchored=False):
    m = BZP_RE.match(s) if anchored else BZP_RE.search(s)
    if not m:
        return None
    v = f"{m.group(1)}/BZP{m.group(2)}" + (f"/{m.group(3)}" if m.group(3) else "")
    return v, m.end()


def parse_ted(s: str, anchored=False):
    m = TED_RE.match(s) if anchored else TED_RE.search(s)
    if not m:
        return None
    if m.group(1):
        return f"{m.group(3)}-{m.group(1)}", m.end()
    return f"{m.group(4)}-{m.group(5)}", m.end()


def parse_value(kind: str, s: str):
    if kind == "account":
        return parse_account(s)
    if kind == "amount":
        return parse_amount(s)
    if kind == "date":
        r = parse_date(s)
        return (r[0], r[2]) if r and r[1] == 0 else None
    if kind == "email":
        return parse_email(s)
    if kind == "token":
        return parse_token(s)
    if kind == "bzp":
        return parse_bzp(s, anchored=True)
    if kind == "ted":
        return parse_ted(s, anchored=True)
    if kind.startswith("digits"):
        return parse_digits(s, int(kind[6:]))
    raise ValueError(f"unknown value type {kind}")
