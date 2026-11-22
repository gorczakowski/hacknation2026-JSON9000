"""Find the passage of an appeal that states the challenged action (it decides czynnosc_powod_id / czynnosc_dzien)."""
import re

START = re.compile(r"(wnosz[ęe]\s+(odwołanie\s+)?(wobec|od|o)\b|odwołanie\s+od\s+czynno|wobec\s*:)", re.I)
END = re.compile(r"(zarzut\s+nr|zarzucam|zarzuty|uzasadnienie|w\s+zwi[aą]zku\s+z\s+powy)", re.I)


def action_passage(text: str, max_len: int = 700) -> str:
    t = re.sub(r"\s+", " ", text)
    m = START.search(t)
    if not m:
        m2 = re.search(r"Zamawiający\s+w\s+dniu", t)
        if not m2:
            return t[:max_len]
        start = m2.start()
    else:
        start = m.start()
    end_m = END.search(t, start + 10)
    end = end_m.start() if end_m and end_m.start() - start < max_len else start + max_len
    return t[start:end].strip()


def first_item(passage: str) -> str:
    """In '... polegających na: 1) X; 2) Y' keep only X."""
    m = re.search(r"\b1\s*[).]\s*(.*?)(?=(;|,)?\s*\b2\s*[).]\s|$)", passage)
    return m.group(1) if m else passage


# Ordered keyword rules for czynnosc_powod_id (first match wins). Kept here for the benchmark;
# the production version lives in config.
POWOD_RULES = [
    (10, r"zaniecha\w*\s+unieważni|nie\s+unieważni"),
    (9, r"unieważni"),
    (8, r"zaniecha\w*\s+wyklucz|nie\s+wykluczy"),
    (3, r"wyklucz"),
    (7, r"zaniecha\w*\s+odrzuc|nie\s+odrzuci"),
    (1, r"odrzuc"),
    (2, r"wyb[oó]r\w*\s+(jako\s+)?(najkorzystniejsz\w*\s+)?ofert|wybra\w*\s+ofert|najkorzystniejsz"),
    (6, r"odpowiedzi|wyjaśnie"),
    (5, r"modyfik|zmian\w*\s+(treści\s+)?(swz|specyfikacji)|zmieni\w*\s+(treść\s+)?(swz|specyfikacj)"),
    (12, r"projektowan\w*\s+postanowie"),
    (11, r"opis\w*\s+przedmiotu\s+zamówienia|\bopz\b"),
    (4, r"specyfikacj|\bswz\b|ogłoszeni\w*\s+o\s+zamówieniu"),
]


def powod_by_rules(passage: str):
    item = first_item(passage).lower()
    for pid, pat in POWOD_RULES:
        if re.search(pat, item):
            return pid
    return None
