"""Where on the page a value is (used by the UI only; the batch pipeline never imports this).

Page geometry comes from three sources, all as "segments" (text + box, 0-1 coordinates, top-left origin):
  - text layer: one segment per glyph (pypdfium2 character boxes) - exact
  - Tesseract: one segment per word - exact to the word, sliced by character position inside a word
  - MinerU: one segment per text block - sliced by character position when the block is one line; in a
    multi-line block the line band is estimated from the character position (marked approximate)

A value is found by searching its text in the page with everything but letters and digits removed and
diacritics folded, so "PL62 2170 0008 …" matches "PL6221700008…" and OCR spacing does not matter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import pypdfium2 as pdfium

from .extract import fold
from .reading import PDFIUM_LOCK


@dataclass
class Seg:
    text: str
    box: tuple[float, float, float, float]  # x0, y0, x1, y1 in 0-1 page coordinates, top-left origin
    split: bool = True  # the box may be sliced horizontally by character position
    lines: int = 1  # estimated number of text lines in the box (MinerU blocks)


@dataclass
class PageGeom:
    width: float  # points
    height: float
    segs: list[Seg]
    joiner: str  # "" for glyphs, " " for words/blocks
    kind: str  # "text" | "ocr"
    keys: str = field(init=False, default="")  # searchable form of the page
    src: list[tuple[int, int]] = field(init=False, default_factory=list)  # keys[i] -> (segment, char in segment)
    glued: list[bool] = field(init=False, default_factory=list)  # keys[i] directly follows keys[i-1] in the page

    def __post_init__(self):
        out, src, glued = [], [], []
        prev = None
        for si, s in enumerate(self.segs):
            for ci, ch in enumerate(s.text):
                k = key_char(ch)
                if k:
                    glued.append(prev is not None and (prev == (si, ci - 1) or (self.joiner == "" and prev == (si - 1, 0))))
                    out.append(k)
                    src.append((si, ci))
                    prev = (si, ci)
                else:
                    prev = None
        self.keys, self.src, self.glued = "".join(out), src, glued


def key_char(ch: str) -> str:
    return fold(ch).lower() if ch.isalnum() else ""


def keys_of(s: str) -> str:
    return "".join(key_char(c) for c in s)


# --- geometry -----------------------------------------------------------------------------------------------

def page_geometry(path, ocr_boxes: dict | None = None) -> list[PageGeom]:
    """Geometry for every page: OCR boxes where the page was OCR'd, glyph boxes from the text layer elsewhere."""
    ocr_boxes = ocr_boxes or {}
    pages = []
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(path))
        try:
            for i in range(len(pdf)):
                page = pdf[i]
                w, h = page.get_size()
                if i in ocr_boxes:
                    segs = [_ocr_seg(b, ocr_boxes[i]) for b in ocr_boxes[i]]
                    pages.append(PageGeom(w, h, [s for s in segs if s], " ", "ocr"))
                    continue
                tp = page.get_textpage()
                n = tp.count_chars()
                text = tp.get_text_range()
                segs = []
                for ci in range(min(n, len(text))):
                    l, b, r, t = tp.get_charbox(ci, loose=True)
                    segs.append(Seg(text[ci], (l / w, 1 - t / h, r / w, 1 - b / h)))
                pages.append(PageGeom(w, h, segs, "", "text"))
        finally:
            pdf.close()
    return pages


def _ocr_seg(b: dict, page_boxes: list[dict]) -> Seg | None:
    bb = b.get("bbox")
    if not bb or len(bb) != 4:
        return None
    x0, y0, x1, y1 = (float(v) for v in bb)
    split, lines = bool(b.get("split", True)), 1
    if split and " " in b["text"].strip():  # a MinerU block: slice only if it is a single line
        heights = sorted(p["bbox"][3] - p["bbox"][1] for p in page_boxes if p.get("bbox"))
        line_h = heights[max(0, len(heights) // 5)] if heights else 0.0
        split = line_h > 0 and (y1 - y0) < 1.6 * line_h
        if not split and line_h > 0:
            lines = max(2, round((y1 - y0) / line_h))
    return Seg(b["text"], (x0, y0, x1, y1), split, lines)


# --- search -------------------------------------------------------------------------------------------------

def _find_all(hay: str, needle: str) -> list[int]:
    out, i = [], hay.find(needle)
    while i >= 0:
        out.append(i)
        i = hay.find(needle, i + 1)
    return out


def _cuts_number(g: PageGeom, a: int, b: int) -> bool:
    """True if [a, b) starts or ends inside a longer run of digits (e.g. "1.09.2026" inside "11.09.2026")."""
    k = g.keys
    if a > 0 and k[a].isdigit() and k[a - 1].isdigit() and g.glued[a]:
        return True
    return b < len(k) and k[b - 1].isdigit() and k[b].isdigit() and g.glued[b]


def _match(g: PageGeom, nk: str, frac: float | None) -> tuple[int, int] | None:
    """Key range [a, b) of the best occurrence of needle keys `nk` in the page."""
    if len(nk) > 80:  # long text: match its beginning and end, highlight everything between
        head, tail = nk[:30], nk[-30:]
        for a in _find_all(g.keys, head):
            b = g.keys.find(tail, a + len(head), a + int(len(nk) * 1.5) + 30)
            if b >= 0:
                return a, b + len(tail)
        hits = _find_all(g.keys, nk[:40])
        return (hits[0], hits[0] + 40) if hits else None
    hits = [a for a in _find_all(g.keys, nk) if not _cuts_number(g, a, a + len(nk))]
    if not hits:
        return None
    if frac is not None and len(hits) > 1 and g.keys:
        best = min(hits, key=lambda a: abs(a / len(g.keys) - frac))
    else:
        best = hits[0]
    return best, best + len(nk)


def _boxes(g: PageGeom, a: int, b: int) -> list[tuple]:
    """Rectangles (x0, y0, x1, y1, approximate) covering keys [a, b): glyph boxes merged per line, word boxes
    sliced by character position, multi-line OCR blocks narrowed to the estimated band of lines."""
    spans: dict[int, list[int]] = {}
    for si, ci in g.src[a:b]:
        lo_hi = spans.setdefault(si, [ci, ci])
        lo_hi[0], lo_hi[1] = min(lo_hi[0], ci), max(lo_hi[1], ci)
    rects = []
    for si in sorted(spans):
        s = g.segs[si]
        x0, y0, x1, y1 = s.box
        lo, hi = spans[si]
        n = len(s.text)
        approx = False
        if n > 1 and s.split and (lo > 0 or hi < n - 1):
            x0, x1 = x0 + (x1 - x0) * lo / n, x0 + (x1 - x0) * (hi + 1) / n
        elif s.lines > 1 and (lo > 0 or hi < n - 1):
            k, lh = s.lines, (y1 - y0) / s.lines
            l0, l1 = int(lo / n * k), min(k - 1, int(hi / n * k))
            y0, y1 = y0 + max(0, l0 - 0.15) * lh, y0 + min(k, l1 + 1.15) * lh  # a little slack each side
            approx = True
        rects.append((x0, y0, x1, y1, approx))
    merged: list[list] = []
    for r in rects:
        if merged:
            m = merged[-1]
            h = min(m[3] - m[1], r[3] - r[1])
            same_line = h > 0 and min(m[3], r[3]) - max(m[1], r[1]) > 0.5 * h
            if same_line and r[0] - m[2] < 3 * max(h, 0.004) and r[0] >= m[0] - 0.002:
                m[1], m[2], m[3] = min(m[1], r[1]), max(m[2], r[2]), max(m[3], r[3])
                m[4] = m[4] or r[4]
                continue
        merged.append(list(r))
    return [tuple(m) for m in merged if m[2] > m[0] and m[3] > m[1]]


def locate(pages: list[PageGeom], needles: list[str], page_hint: int | None = None,
           frac_hint: float | None = None, narrow: list[str] | None = None) -> list[dict]:
    """Boxes of the first needle found ({"p": page, "x0", "y0", "x1", "y1"}); the hinted page is searched first.
    `narrow`: if one of these is found inside the match (e.g. one name inside a list of names), box only that."""
    order = list(range(len(pages)))
    if page_hint is not None and 0 <= page_hint < len(pages):
        order.remove(page_hint)
        order.insert(0, page_hint)
    seen = set()
    for nd in needles:
        nk = keys_of(nd)
        if len(nk) < 3 or nk in seen:
            continue
        seen.add(nk)
        for p in order:
            m = _match(pages[p], nk, frac_hint if p == page_hint else None)
            if m:
                a, b = m
                for nn in narrow or []:
                    sk = keys_of(nn)
                    i = pages[p].keys.find(sk, a, b) if len(sk) >= 3 and len(sk) < b - a else -1
                    if i >= 0:
                        a, b = i, i + len(sk)
                        break
                return [{"p": p, "x0": r[0], "y0": r[1], "x1": r[2], "y1": r[3], "approx": r[4]}
                        for r in _boxes(pages[p], a, b)]
    return []


# --- what to search for -------------------------------------------------------------------------------------

MONTHS = ["stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca", "sierpnia", "września",
          "października", "listopada", "grudnia"]


def value_needles(value) -> list[str]:
    """Ways a normalized output value may be written in the document (most specific first)."""
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, float):
        out = [f"{value:.2f}"]
        if value == int(value):
            out.append(str(int(value)))
        return out
    if isinstance(value, int):
        return [str(value)] if abs(value) >= 100 else []
    s = str(value).strip()
    if not s:
        return []
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        y, mo, d = m.groups()
        try:
            date(int(y), int(mo), int(d))
        except ValueError:
            return [s]
        di, mi = int(d), int(mo)
        return [f"{d}.{mo}.{y}", f"{di}.{mo}.{y}", f"{d}.{mi}.{y}", f"{di} {MONTHS[mi - 1]} {y}",
                f"{d} {MONTHS[mi - 1]} {y}", s, f"{d}-{mo}-{y}", f"{d}/{mo}/{y}"]
    out = [s]
    if re.fullmatch(r"[A-Z]{2}\d{26}", s.replace(" ", "")):  # IBAN: the document may omit the country code
        out.append(s.replace(" ", "")[2:])
    m = re.fullmatch(r"(\d{6,})-(\d{4})", s)  # TED NNNNNN-YYYY, often written YYYY/S ###-NNNNNN
    if m:
        out.append(m.group(1))
    return out
