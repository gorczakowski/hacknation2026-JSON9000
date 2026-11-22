"""PDF -> Document. Text layer via pypdfium2; pages without text go to an OCR backend (MinerU) if available."""

from __future__ import annotations

import json
import os
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pypdfium2 as pdfium

log = logging.getLogger(__name__)
PDFIUM_LOCK = threading.Lock()  # PDFium is not thread-safe
PAGE_BREAK = "\n\f\n"


@dataclass
class Document:
    path: Path
    pages: list[str]
    source: str  # "text" | "ocr" | "text+ocr" | "none"
    error: str | None = None  # set when a page needed OCR and OCR failed (reported separately from "unrelated")
    # UI only (load_document(layout=True)): {page index: [{"bbox": [x0, y0, x1, y1] 0-1, "text", "split"}]} for
    # OCR'd pages - Tesseract words or MinerU blocks. Empty in batch runs.
    ocr_boxes: dict = field(default_factory=dict, repr=False)
    text: str = field(init=False)
    norm: str = field(init=False)  # text prepared for matching (dot leaders removed, spaced letters joined)

    def __post_init__(self):
        self.text = PAGE_BREAK.join(self.pages)
        self.norm = prepare(self.text)


def prepare(text: str) -> str:
    t = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    t = re.sub(r"\.{4,}|…{2,}|-{6,}|_{4,}", " ", t)  # dot leaders / rules
    # "O D W O Ł A N I E" -> "ODWOŁANIE"
    t = re.sub(r"\b(?:\w ){3,}\w\b", lambda m: m.group(0).replace(" ", ""), t)
    t = re.sub(r"[ \t]+", " ", t)
    return t


def read_text_layer(path: Path) -> list[str]:
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(path))
        try:
            return [pdf[i].get_textpage().get_text_range() for i in range(len(pdf))]
        finally:
            pdf.close()


class MinerUOCR:
    """Runs MinerU in its own interpreter (it has heavy dependencies). Local only, never the remote service."""

    def __init__(self, python: str | None, tier: str = "flash", cache_dir: Path | None = None):
        self.python = python
        self.tier = tier
        self.cache_dir = cache_dir
        self.lock = threading.Lock()  # one OCR job at a time (CPU bound, large models)

    def available(self) -> bool:
        return bool(self.python) and Path(self.python).exists()

    def ocr(self, path: Path, pages: list[int] | None = None) -> list[str]:
        """OCR the whole PDF (MinerU works per document); raises OCRError with MinerU's own message on failure.

        If MinerU is installed in this interpreter (the Docker image), it runs in-process, so its OCR models are
        loaded once per run instead of once per scan. Otherwise it runs in its own venv as a subprocess."""
        return self._run(path, layout=False)["pages"]

    def ocr_layout(self, path: Path, pages: list[int] | None = None) -> tuple[list[str], list[list[dict]]]:
        """Same text as ocr(), plus the text blocks with their boxes per page (for the UI)."""
        r = self._run(path, layout=True)
        boxes = [[{**b, "split": True} for b in page] for page in r["blocks"]]
        return r["pages"], boxes

    def _run(self, path: Path, layout: bool) -> dict:
        if not self.available():
            raise OCRError("OCR engine not available (MinerU interpreter not found; set ocr.python in pipeline.yaml)")
        if self.python == sys.executable:
            with self.lock:
                try:
                    from ._mineru_worker import parse_layout, parse_pages
                    if layout:
                        texts, blocks = parse_layout(str(path), self.tier)
                        return {"pages": texts, "blocks": blocks}
                    return {"pages": parse_pages(str(path), self.tier)}
                except Exception as e:  # noqa: BLE001 - report MinerU's own message
                    raise OCRError(f"MinerU: {e!r}"[:500]) from e
        script = Path(__file__).with_name("_mineru_worker.py")
        env = dict(os.environ)
        if self.cache_dir:  # explicit, writable model/cache location (MinerU and Hugging Face downloads)
            Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
            env.setdefault("HF_HOME", str(Path(self.cache_dir) / "hf"))
            env.setdefault("MODELSCOPE_CACHE", str(Path(self.cache_dir) / "modelscope"))
            env.setdefault("MINERU_CACHE_DIR", str(self.cache_dir))
        with self.lock, tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.json"
            cmd = [self.python, str(script), str(path), str(out), self.tier] + (["layout"] if layout else [])
            r = subprocess.run(cmd, capture_output=True, timeout=1800, env=env)
            if r.returncode != 0 or not out.exists():
                tail = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
                raise OCRError(f"MinerU exit {r.returncode}: {' | '.join(tail)[:500]}")
            return json.loads(out.read_text(encoding="utf-8"))


class OCRError(RuntimeError):
    pass


class TesseractOCR:
    """Tesseract 5 with Polish data (apt: tesseract-ocr tesseract-ocr-pol). Pages are rendered with PDFium and
    OCR'd one by one with a single thread each (several threads per process were 6x slower on 4 cores)."""

    def __init__(self, dpi: int = 300, lang: str = "pol", binary: str = "tesseract"):
        self.dpi, self.lang, self.binary = dpi, lang, binary

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def ocr(self, path: Path, pages: list[int] | None = None) -> list[str]:
        return self._run(path, pages, layout=False)[0]

    def ocr_layout(self, path: Path, pages: list[int] | None = None) -> tuple[list[str], list[list[dict]]]:
        """Same text as ocr() (one tesseract call writes both txt and tsv), plus word boxes per page (for the UI)."""
        return self._run(path, pages, layout=True)

    def _run(self, path: Path, pages: list[int] | None, layout: bool):
        if not self.available():
            raise OCRError("tesseract not installed (apt install tesseract-ocr tesseract-ocr-pol)")
        with PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(str(path))
            try:
                n = len(pdf)
                images = {i: pdf[i].render(scale=self.dpi / 72).to_pil().convert("L") for i in (pages or range(n))}
            finally:
                pdf.close()
        out = [""] * n
        words: list[list[dict]] = [[] for _ in range(n)]
        env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
        with tempfile.TemporaryDirectory() as td:
            for i, img in images.items():
                f = Path(td) / f"p{i}.png"
                img.save(f)
                if not layout:
                    r = subprocess.run([self.binary, str(f), "stdout", "-l", self.lang, "--psm", "3"],
                                       capture_output=True, timeout=300, env=env)
                else:
                    base = Path(td) / f"o{i}"
                    r = subprocess.run([self.binary, str(f), str(base), "-l", self.lang, "--psm", "3", "txt", "tsv"],
                                       capture_output=True, timeout=300, env=env)
                if r.returncode != 0:
                    raise OCRError(f"tesseract exit {r.returncode}: {r.stderr.decode('utf-8', 'replace')[:300]}")
                if not layout:
                    out[i] = r.stdout.decode("utf-8", "replace")
                    continue
                out[i] = base.with_suffix(".txt").read_text(encoding="utf-8", errors="replace")
                w, h = img.size
                for line in base.with_suffix(".tsv").read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
                    c = line.split("\t")
                    if len(c) >= 12 and c[0] == "5" and c[11].strip():
                        x, y, bw, bh = (int(v) for v in c[6:10])
                        words[i].append({"bbox": [x / w, y / h, (x + bw) / w, (y + bh) / h], "text": c[11], "split": True})
        return out, words


def pages_needing_ocr(pages: list[str], min_chars_per_page: int = 40) -> list[int]:
    return [i for i, p in enumerate(pages) if len(p.strip()) < min_chars_per_page]


def load_document(path: Path, ocr: MinerUOCR | None = None, min_chars_per_page: int = 40,
                  cached_ocr: Path | None = None, layout: bool = False) -> Document:
    """Text layer per page; pages with (almost) no text get OCR text instead.

    A PDF that mixes text pages and image-only pages keeps its text pages and OCRs the rest.
    If OCR is needed but fails, the Document carries `error` so the caller can report it.
    layout=True (UI only) also keeps the OCR boxes of the OCR'd pages in Document.ocr_boxes; the text is the same.
    """
    pages = read_text_layer(path)
    need = pages_needing_ocr(pages, min_chars_per_page)
    if not need:
        return Document(path, pages, "text")
    ocr_pages, error, boxes = None, None, None
    if cached_ocr and cached_ocr.exists():
        ocr_pages = cached_ocr.read_text(encoding="utf-8").split(PAGE_BREAK)
    elif ocr is not None:
        try:
            if layout and hasattr(ocr, "ocr_layout"):
                ocr_pages, boxes = ocr.ocr_layout(path, need)
            else:
                ocr_pages = ocr.ocr(path, need)
        except OCRError as e:
            error = str(e)
            log.warning("OCR failed for %s: %s", path, e)
    else:
        error = "page(s) without a text layer and OCR disabled"
    if ocr_pages:
        merged = [ocr_pages[i] if i in need and i < len(ocr_pages) else pages[i] for i in range(len(pages))]
        doc = Document(path, merged, "ocr" if len(need) == len(pages) else "text+ocr")
        if boxes:
            doc.ocr_boxes = {i: boxes[i] for i in need if i < len(boxes)}
        return doc
    has_text = len(need) < len(pages)
    return Document(path, pages, "text" if has_text else "none", error=error)


def default_mineru_python() -> str | None:
    """MinerU's interpreter: the current one if MinerU is installed here (Docker image), else ./.venv-mineru."""
    import importlib.util
    if importlib.util.find_spec("mineru") is not None:
        return sys.executable
    root = Path(__file__).resolve().parent.parent
    for cand in (root / ".venv-mineru" / "Scripts" / "python.exe", root / ".venv-mineru" / "bin" / "python"):
        if cand.exists():
            return str(cand)
    return None


class FallbackOCR:
    """Primary engine (MinerU: more accurate, ~15-35 s per scan) with a faster fallback (Tesseract: ~4-8 s).

    The fallback is used for a document when the primary fails on it, and for all documents once
    `fallback_after_s` seconds have passed since the start of the run - a time safety net, like the deadline:
    runs that finish earlier never use it."""

    def __init__(self, primary, fallback, fallback_after_s: float | None):
        self.primary, self.fallback, self.after = primary, fallback, fallback_after_s
        self.t0 = time.time()
        self.used_fallback = 0

    def available(self) -> bool:
        return self.primary.available() or (self.fallback is not None and self.fallback.available())

    def ocr(self, path: Path, pages: list[int] | None = None) -> list[str]:
        return self._run("ocr", path, pages)

    def ocr_layout(self, path: Path, pages: list[int] | None = None):
        return self._run("ocr_layout", path, pages)

    def _run(self, method: str, path: Path, pages: list[int] | None):
        late = self.after is not None and time.time() - self.t0 > self.after
        if not late and self.primary.available():
            try:
                return getattr(self.primary, method)(path, pages)
            except OCRError as e:
                if self.fallback is None or not self.fallback.available():
                    raise
                log.warning("primary OCR failed for %s (%s); using fallback", path.name, e)
        if self.fallback is None or not self.fallback.available():
            return getattr(self.primary, method)(path, pages)
        self.used_fallback += 1
        return getattr(self.fallback, method)(path, pages)


def make_ocr(cfg: dict):
    cfg = cfg or {}

    def build(engine):
        if engine == "tesseract":
            return TesseractOCR(int(cfg.get("tesseract_dpi", 300)))
        if engine == "mineru":
            return MinerUOCR(cfg.get("python") or default_mineru_python(), cfg.get("tier", "flash"),
                             Path(cfg["cache_dir"]) if cfg.get("cache_dir") else None)
        raise ValueError(f"unknown OCR engine {engine}")

    primary = build(cfg.get("engine", "mineru"))
    fb = cfg.get("fallback_engine")
    if not fb or fb == cfg.get("engine"):
        return primary
    return FallbackOCR(primary, build(fb), cfg.get("fallback_after_s"))


if __name__ == "__main__":  # quick manual check
    d = load_document(Path(sys.argv[1]))
    print(d.source, len(d.text))
    print(d.norm[:2000])
