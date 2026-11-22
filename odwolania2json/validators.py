"""Check-digit validation for Polish identifiers (NIP, REGON, PESEL, KRS format, IBAN mod 97).

Reported in the evidence file as an extra signal. Never used to drop or change values: about a third of
the identifiers in the (synthetic) training labels fail their check digits.
"""

from __future__ import annotations

import re


def nip_ok(s: str) -> bool:
    w = [6, 5, 7, 2, 3, 4, 5, 6, 7]
    return len(s) == 10 and s.isdigit() and sum(int(a) * b for a, b in zip(s, w)) % 11 == int(s[9])


def regon_ok(s: str) -> bool:
    if not s.isdigit():
        return False
    if len(s) == 9:
        w = [8, 9, 2, 3, 4, 5, 6, 7]
    elif len(s) == 14:
        w = [2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8]
    else:
        return False
    return sum(int(a) * b for a, b in zip(s, w)) % 11 % 10 == int(s[-1])


def pesel_ok(s: str) -> bool:
    w = [1, 3, 7, 9, 1, 3, 7, 9, 1, 3]
    return len(s) == 11 and s.isdigit() and (10 - sum(int(a) * b for a, b in zip(s, w)) % 10) % 10 == int(s[10])


def krs_ok(s: str) -> bool:
    return len(s) == 10 and s.isdigit()


def iban_ok(s: str) -> bool:
    """ISO 13616 check: move the first 4 chars to the end, letters -> 10..35, number mod 97 == 1.
    A Polish NRB written without the country code (26 digits) is checked as PL + NRB."""
    s = re.sub(r"[\s-]", "", s.upper())
    if s.isdigit() and len(s) == 26:
        s = "PL" + s
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", s):
        return False
    r = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in r)) % 97 == 1


CHECKS = {"nip": nip_ok, "regon": regon_ok, "pesel": pesel_ok, "krs": krs_ok,
          "na_rachunek": iban_ok, "z_rachunku": iban_ok}


def check_all(data, path="") -> dict:
    """Return {path: bool} for every identifier leaf with a value."""
    out = {}
    if isinstance(data, dict):
        for k, v in data.items():
            p = f"{path}.{k}" if path else k
            if k in CHECKS and isinstance(v, str) and v:
                out[p] = CHECKS[k](v)
            else:
                out.update(check_all(v, p))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            out.update(check_all(v, f"{path}[{i}]"))
    return out
