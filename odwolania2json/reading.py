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
        if not self.available():
            raise OCRError("OCR engine not available (MinerU interpreter not found; set ocr.python in pipeline.yaml)")
        if self.python == sys.executable:
            with self.lock:
                try:
                    from ._mineru_worker import parse_pages
                    return parse_pages(str(path), self.tier)
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
            cmd = [self.python, str(script), str(path), str(out), self.tier]
            r = subprocess.run(cmd, capture_output=True, timeout=1800, env=env)
            if r.returncode != 0 or not out.exists():
                tail = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
                raise OCRError(f"MinerU exit {r.returncode}: {' | '.join(tail)[:500]}")
            return json.loads(out.read_text(encoding="utf-8"))["pages"]


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
        env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
        with tempfile.TemporaryDirectory() as td:
            for i, img in images.items():
                f = Path(td) / f"p{i}.png"
                img.save(f)
                r = subprocess.run([self.binary, str(f), "stdout", "-l", self.lang, "--psm", "3"],
                                   capture_output=True, timeout=300, env=env)
                if r.returncode != 0:
                    raise OCRError(f"tesseract exit {r.returncode}: {r.stderr.decode('utf-8', 'replace')[:300]}")
                out[i] = r.stdout.decode("utf-8", "replace")
        return out


def load_document(path: Path, ocr: MinerUOCR | None = None, min_chars_per_page: int = 40,
                  cached_ocr: Path | None = None) -> Document:
    """Text layer per page; pages with (almost) no text get OCR text instead.

    A PDF that mixes text pages and image-only pages keeps its text pages and OCRs the rest.
    If OCR is needed but fails, the Document carries `error` so the caller can report it.
    """
    pages = read_text_layer(path)
    need = [i for i, p in enumerate(pages) if len(p.strip()) < min_chars_per_page]
    if not need:
        return Document(path, pages, "text")
    ocr_pages, error = None, None
    if cached_ocr and cached_ocr.exists():
        ocr_pages = cached_ocr.read_text(encoding="utf-8").split(PAGE_BREAK)
    elif ocr is not None:
        try:
            ocr_pages = ocr.ocr(path, need)
        except OCRError as e:
            error = str(e)
            log.warning("OCR failed for %s: %s", path, e)
    else:
        error = "page(s) without a text layer and OCR disabled"
    if ocr_pages:
        merged = [ocr_pages[i] if i in need and i < len(ocr_pages) else pages[i] for i in range(len(pages))]
        return Document(path, merged, "ocr" if len(need) == len(pages) else "text+ocr")
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
        late = self.after is not None and time.time() - self.t0 > self.after
        if not late and self.primary.available():
            try:
                return self.primary.ocr(path, pages)
            except OCRError as e:
                if self.fallback is None or not self.fallback.available():
                    raise
                log.warning("primary OCR failed for %s (%s); using fallback", path.name, e)
        if self.fallback is None or not self.fallback.available():
            return self.primary.ocr(path, pages)
        self.used_fallback += 1
        return self.fallback.ocr(path, pages)


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
