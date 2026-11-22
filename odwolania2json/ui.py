"""Browser UI: upload a PDF, see the JSON and where on the page every value came from.

    python -m odwolania2json.ui                      # http://127.0.0.1:8000
    python -m odwolania2json.ui --samples hacknation-2026/dane --host 0.0.0.0

Runs the same pipeline as the batch command (same config, same OCR, same model) on one document at a time and
adds what the batch run does not need: page images, value locations (glyph boxes on text PDFs, OCR word/block
boxes on scans), the classification rules that fired, and switching document types on and off. Standard library
only (http.server); the batch command never imports this module.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import queue
import re
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse, parse_qs

import pypdfium2 as pdfium
import yaml

from . import layout as L
from .__main__ import ROOT, load_env
from .derive import REGISTRY
from .extract import fold
from .llm import LLM
from .pipeline import DocType, load_doctype, process_document
from .reading import PDFIUM_LOCK, load_document, make_ocr, pages_needing_ocr, read_text_layer
from .validate import validate

log = logging.getLogger("odwolania2json.ui")
STATIC = Path(__file__).with_name("ui_static")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".woff2": "font/woff2", ".png": "image/png", ".svg": "image/svg+xml",
         ".txt": "text/plain; charset=utf-8"}
RULE_METHODS = ("label:", "regex", "nth:", "find:", "place_date", "first_line", "names_after:", "const",
                "passage_rules", "passage_date", "signatories")
MODEL_METHODS = ("model", "passage_model", "model_document", "header_line")


# --- configuration (document types can be switched on and off) ---------------------------------------------

class Engine:
    def __init__(self, args):
        self.config_dir: Path = args.config
        self.pipeline_cfg = yaml.safe_load((self.config_dir / "pipeline.yaml").read_text(encoding="utf-8"))
        self.lock = threading.Lock()
        self.catalog = []  # {id, path, group, enabled, name, description, error}
        self.added_dir: Path | None = None  # where YAML files added in the browser are kept (set by main)
        folder = self.config_dir / "doctypes"
        for p in sorted(folder.glob("*.y*ml")) if folder.is_dir() else []:
            self._add(p, "default", True)
        for p in args.doctypes:
            for f in (sorted(p.glob("*.y*ml")) if p.is_dir() else [p]):
                known = next((c for c in self.catalog if c["path"].resolve() == f.resolve()), None)
                if known:
                    known["enabled"] = True
                else:
                    self._add(f, "extra", True)
        self.doctypes: list[DocType] = []
        problems = self.apply([c["id"] for c in self.catalog if c["enabled"]])
        if problems:
            raise SystemExit("Configuration errors:\n  " + "\n  ".join(problems))

        load_env(ROOT / ".env")
        ocfg = dict(self.pipeline_cfg.get("ocr", {}) or {})
        if args.ocr_engine:
            ocfg["engine"] = args.ocr_engine
        if ocfg.get("cache_dir"):
            ocfg["cache_dir"] = str(ROOT / ocfg["cache_dir"])
        ocfg["fallback_after_s"] = None  # the batch run's time safety net makes no sense in a long-running server
        self.ocr_engine = None if args.no_ocr else ocfg.get("engine", "mineru")
        self.ocr = None if args.no_ocr else make_ocr(ocfg)
        lcfg = self.pipeline_cfg.get("llm", {}) or {}
        self.llm = LLM(lcfg) if (lcfg.get("enabled", True) and not args.no_llm) else None
        self.model_off = None if self.llm else "disabled"
        if self.llm is not None and not self.llm.available:
            log.warning("model off: no API key in $%s (.env)", lcfg.get("api_key_env"))
            self.llm, self.model_off = None, "no_key"
        self.model_name = lcfg.get("model") if self.llm else None
        self.hcfg = self.pipeline_cfg.get("hybrid", {}) or {}
        self.head_chars = (self.pipeline_cfg.get("classify", {}) or {}).get("head_chars", 4000)

    def _add(self, path: Path, group: str, enabled: bool):
        entry = {"id": f"{group}:{path.name}", "path": path, "group": group, "enabled": enabled,
                 "name": path.stem, "description": "", "error": None}
        try:
            cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            entry["name"] = cfg.get("name", path.stem)
            entry["description"] = ((cfg.get("classify") or {}).get("description") or "").strip()
        except Exception as e:  # noqa: BLE001 - shown in the UI
            entry["error"] = f"{type(e).__name__}: {e}"[:300]
        self.catalog.append(entry)

    def apply(self, enabled_ids: list[str]) -> list[str]:
        """Load the chosen document types and validate them; on errors keep the previous set."""
        chosen = [c for c in self.catalog if c["id"] in set(enabled_ids)]
        by_name: dict[str, DocType] = {}
        problems = []
        for c in chosen:
            try:
                dt = load_doctype(c["path"], self.config_dir)
                by_name[dt.name] = dt
            except Exception as e:  # noqa: BLE001
                problems.append(f"{c['path'].name}: {type(e).__name__}: {e}"[:300])
        doctypes = list(by_name.values())
        if not problems:
            problems = validate(doctypes, REGISTRY)
        if problems:
            return problems
        with self.lock:
            self.doctypes = doctypes
            for c in self.catalog:
                c["enabled"] = c["id"] in set(enabled_ids)
        return []

    def add_uploaded(self, filename: str, data: bytes) -> list[str]:
        """A document-type YAML added in the browser: checked on its own, then listed (switched off)."""
        name = re.sub(r"[^\w.\-]", "_", Path(filename).name)[:80] or "typ.yaml"
        if not name.lower().endswith((".yaml", ".yml")):
            return ["To nie jest plik YAML (.yaml albo .yml)."]
        try:
            text = data.decode("utf-8")
            cfg = yaml.safe_load(text)
        except (UnicodeDecodeError, yaml.YAMLError) as e:
            return [f"Nie da się odczytać pliku: {e}"[:300]]
        if not isinstance(cfg, dict) or not cfg.get("name") or not isinstance(cfg.get("classify"), dict):
            return ["Plik nie wygląda na typ dokumentu: brakuje pól name albo classify."]
        dst = self.added_dir / name
        dst.write_text(text, encoding="utf-8")
        try:
            dt = load_doctype(dst, self.config_dir)
            problems = validate([dt], REGISTRY)
        except Exception as e:  # noqa: BLE001 - shown in the UI
            problems = [f"{name}: {type(e).__name__}: {e}"[:300]]
        if problems:
            dst.unlink(missing_ok=True)
            return problems
        with self.lock:
            self.catalog = [c for c in self.catalog if c["id"] != f"added:{name}"]
        self._add(dst, "added", False)
        return []

    def remove(self, cid: str) -> list[str]:
        c = next((c for c in self.catalog if c["id"] == cid), None)
        if not c or c["group"] != "added":
            return ["Można usunąć tylko typ dodany w przeglądarce."]
        problems = self.apply([x["id"] for x in self.catalog if x["enabled"] and x["id"] != cid])
        if problems:
            return problems
        with self.lock:
            self.catalog = [x for x in self.catalog if x["id"] != cid]
        c["path"].unlink(missing_ok=True)
        return []

    def describe(self) -> list[dict]:
        return [{k: (v.name if isinstance(v, Path) else v) for k, v in c.items()} for c in self.catalog]


# --- jobs -----------------------------------------------------------------------------------------------------

@dataclass
class Job:
    id: str
    name: str
    path: Path
    owned: bool  # uploaded (deleted with the job) vs. a sample file
    use_llm: bool
    status: str = "queued"  # queued | running | done | error
    stage: str = "queued"  # queued | read | ocr | extract | model | done
    error: str | None = None
    created: float = field(default_factory=time.time)
    doc: object = None
    geoms: list = None
    view: dict = None
    images: dict = field(default_factory=dict)
    n_pages: int = 0
    t_read: float = 0.0


class Jobs:
    def __init__(self, engine: Engine, work_dir: Path, keep: int = 30):
        self.engine, self.work_dir, self.keep = engine, work_dir, keep
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    def add(self, name: str, path: Path, owned: bool, use_llm: bool) -> Job:
        job = Job(uuid.uuid4().hex[:12], name, path, owned, use_llm)
        with PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(str(path))
            job.n_pages = len(pdf)
            pdf.close()
        with self.lock:
            self.jobs[job.id] = job
            old = sorted(self.jobs.values(), key=lambda j: j.created)[:-self.keep]
            for j in old:
                if j.status in ("done", "error"):
                    self.jobs.pop(j.id, None)
                    if j.owned:
                        j.path.unlink(missing_ok=True)
        self.q.put(job.id)
        return job

    def rerun(self, job: Job, use_llm: bool):
        job.use_llm, job.status, job.stage, job.error = use_llm, "queued", "queued", None
        self.q.put(job.id)

    def get(self, jid: str) -> Job | None:
        return self.jobs.get(jid)


    def _worker(self):
        while True:
            job = self.jobs.get(self.q.get())
            if job is None:
                continue
            job.status = "running"
            try:
                self._process(job)
                job.status, job.stage = "done", "done"
            except Exception as e:  # noqa: BLE001 - shown in the UI
                log.exception("processing %s failed", job.name)
                job.status, job.error = "error", f"{type(e).__name__}: {e}"[:500]

    def _process(self, job: Job):
        eng = self.engine
        if job.doc is None:  # read once; a re-run with other document types or without the model reuses the text
            t0 = time.time()
            job.stage = "read"
            if pages_needing_ocr(read_text_layer(job.path)) and eng.ocr is not None:
                job.stage = "ocr"
            job.doc = load_document(job.path, eng.ocr, layout=True)
            job.geoms = L.page_geometry(job.path, job.doc.ocr_boxes)
            job.t_read = time.time() - t0
        llm = eng.llm if job.use_llm else None
        job.stage = "model" if llm else "extract"
        with eng.lock:
            doctypes = list(eng.doctypes)
        t0 = time.time()
        data, meta, evidence = process_document(job.doc, doctypes, REGISTRY, eng.head_chars, llm=llm,
                                                hybrid_cfg=eng.hcfg)
        view = build_view(job, doctypes, data, meta, evidence, eng)
        view["timing"] = {"read_s": round(job.t_read, 1), "process_s": round(time.time() - t0, 1)}
        for n in range(min(job.n_pages, 4)):  # first pages ready before the browser asks (scans render slowly)
            render_page(job, n)
        job.view = view


# --- the view: fields with provenance and boxes ------------------------------------------------------------------

def _split_comment(line: str) -> tuple[str, str]:
    in_str = esc = False
    for i, ch in enumerate(line):
        if esc:
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str and line.startswith("//", i):
            return line[:i], line[i + 2:]
    return line, ""


def template_comments(text: str) -> dict[str, str]:
    """{"Odwolujacy.nip": "NIP Odwołującego ..."} from the comments in an output template."""
    stack: list[str | None] = []
    out = {}
    for line in text.splitlines():
        code, comment = _split_comment(line)
        st = code.strip()
        m = re.match(r'"([^"]+)"\s*:\s*(.*)$', st)
        if m:
            k, rest = m.group(1), m.group(2).strip().rstrip(",")
            if comment.strip():
                out[".".join([s for s in stack if s] + [k])] = comment.strip()
            if (rest.startswith("{") and not rest.endswith("}")) or (rest.startswith("[") and not rest.endswith("]")):
                stack.append(k)
            continue
        for ch in st:
            if ch in "{[":
                stack.append(None)
            elif ch in "}]" and stack:
                stack.pop()
    return out


def _noidx(path: str) -> str:
    return re.sub(r"\[\d*\]", "", path)


def _leaves(obj, path=""):
    """(group, path, value) for every leaf; lists of scalars are one leaf; empty lists of objects are reported."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if isinstance(v, dict):
                yield from _leaves(v, p)
            elif isinstance(v, list) and (not v or isinstance(v[0], dict)):
                if not v:
                    yield path, p, []
                for i, item in enumerate(v):
                    yield from _leaves(item, f"{p}[{i}]")
            else:
                yield path, p, v


def _ev_value_key(v) -> str:
    return L.keys_of(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))


def _pick_evidence(path: str, value, evidence: list) -> dict | None:
    gen = re.sub(r"\[\d+\]", "[]", path)

    def covers(f: str) -> bool:  # "a[0].b" / "a[].b" / "a[]" (a whole list) / "a[].b" for "a[0].b[1].c"
        fg = re.sub(r"\[\d+\]", "[]", f)
        return f == path or fg == gen or gen.startswith(fg + ".") or gen.startswith(fg + "[]") or \
            (fg.endswith("[]") and gen.startswith(fg))

    cands = [e for e in evidence if covers(str(e.get("field", "")))]
    if not cands:
        return None
    vk = _ev_value_key(value)
    for group in ([e for e in cands if e.get("field") == path], cands):  # this exact list item first
        same = [e for e in group if _ev_value_key(e.get("value")) == vk]
        if same:
            return same[-1]
    exact = [e for e in cands if e.get("field") == path]
    return (exact or cands)[-1]


def _provenance(ev: dict | None, value) -> str:
    if value in (None, "", []):
        return "empty"
    if ev is None:
        return "derived"
    m = str(ev.get("method", ""))
    if m.startswith(MODEL_METHODS):
        return "model"
    if m.startswith(RULE_METHODS):
        return "rules"
    return "derived"


def _rule_cfg(dt: DocType, p: str):
    f = (dt.cfg.get("fields") or {}).get(p)
    if f:
        return f
    for lp, ld in (dt.cfg.get("lists") or {}).items():
        if p.startswith(lp + "."):
            rest = p[len(lp) + 1:]
            if (ld.get("fields") or {}).get(rest):
                return ld["fields"][rest]
            if ld.get("name_field") == rest:
                return ld.get("from_names")
    return None


def _yaml(obj) -> str:
    return yaml.safe_dump(obj, allow_unicode=True, sort_keys=False, default_flow_style=None, width=10_000).strip()


def _hints(doc, start: int) -> tuple[int, float]:
    norm = doc.norm
    page = norm.count("\f", 0, start)
    ps = norm.rfind("\f", 0, start) + 1
    pe = norm.find("\f", start)
    pe = len(norm) if pe < 0 else pe
    return page, (start - ps) / max(1, pe - ps)


def _locate(job: Job, ev: dict | None, value) -> list[dict]:
    doc, geoms = job.doc, job.geoms
    if isinstance(value, list):
        boxes = []
        for v in value:
            boxes += L.locate(geoms, L.value_needles(v))
        return boxes
    vals = L.value_needles(value)
    if ev:
        s, e = ev.get("start", -1), ev.get("end", -1)
        if isinstance(s, int) and s >= 0 and e > s:  # the rule's own match, on its page and at its position
            page, frac = _hints(doc, s)
            boxes = L.locate(geoms, [doc.norm[s:e]], page, frac, narrow=vals)
            if boxes:
                return boxes
            boxes = L.locate(geoms, vals, page, frac)
            if boxes:
                return boxes
    boxes = L.locate(geoms, vals)
    if not boxes and ev and ev.get("context") and _provenance(ev, value) != "model":
        boxes = L.locate(geoms, [ev["context"][:120]])  # e.g. the challenged-action passage behind the 1-12 code
    return boxes


def _class_rules(doc, dt: DocType, head_chars: int) -> list[dict]:
    """Which classification rules fired (same matching as pipeline.classify)."""
    head, full = fold(doc.norm[:head_chars]), fold(doc.norm)
    out = []
    for r in dt.cfg["classify"]["rules"]:
        hit = bool(re.search(fold(r["pattern"]), head if not r.get("anywhere") else full, re.I | re.M))
        out.append({"pattern": r["pattern"], "weight": r["weight"], "hit": hit, "anywhere": bool(r.get("anywhere"))})
    return out


def build_view(job: Job, doctypes: list[DocType], data, meta, evidence, eng: Engine) -> dict:
    doc = job.doc
    scores = meta.get("class_scores", {})
    classify = []
    for dt in doctypes:
        c = dt.cfg["classify"]
        classify.append({"name": dt.name, "file": dt.source.name if dt.source else "", "score": scores.get(dt.name, 0),
                         "threshold": c["threshold"], "chosen": dt.name == meta.get("type"),
                         "rules": _class_rules(doc, dt, eng.head_chars)})
    fields = []
    dt = next((d for d in doctypes if d.name == meta.get("type")), None)
    if dt is not None:
        comments = template_comments(dt.template_text)
        checks = meta.get("checks", {})
        for group, path, value in _leaves(data):
            ev = _pick_evidence(path, value, evidence)
            prov = _provenance(ev, value)
            boxes = _locate(job, ev, value) if prov != "empty" else []
            cfg = _rule_cfg(dt, _noidx(path)) if prov in ("rules", "empty") else None
            fields.append({
                "group": group, "path": path, "key": path.rsplit(".", 1)[-1],
                "desc": comments.get(_noidx(path), ""), "value": value, "prov": prov,
                "shown": json.dumps(value, ensure_ascii=False) if isinstance(value, (int, float)) and not isinstance(value, bool) else None,
                "method": (ev or {}).get("method"), "context": (ev or {}).get("context", ""),
                "rule": _yaml(cfg) if cfg else None, "boxes": boxes, "check": checks.get(path),
                "empty_list": isinstance(value, list) and not value and group != path.rsplit(".", 1)[0],
            })
    return {
        "id": job.id, "file": job.name, "source": doc.source, "read_error": doc.error,
        "ocr_engine": eng.ocr_engine if doc.source in ("ocr", "text+ocr") else None,
        "type": meta.get("type"), "type_decided_by": meta.get("type_decided_by"),
        "fields_decided_by": meta.get("fields_decided_by"), "tie": scores.get("_tie"),
        "model": eng.model_name if job.use_llm and eng.llm else None,
        "type_file": dt.source.name if dt and dt.source else None,
        "classify": classify, "fields": fields, "output": data,
        "pages": [{"w": g.width, "h": g.height, "kind": g.kind} for g in job.geoms],
    }


def render_page(job: Job, n: int) -> bytes:
    if n in job.images:
        return job.images[n]
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(job.path))
        try:
            page = pdf[n]
            w, _ = page.get_size()
            img = page.render(scale=min(2.2, 1500 / w)).to_pil().convert("RGB")
        finally:
            pdf.close()
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82, optimize=True)
    job.images[n] = buf.getvalue()
    return job.images[n]


# --- HTTP -------------------------------------------------------------------------------------------------------

def make_handler(eng: Engine, jobs: Jobs, samples: list[tuple[str, Path]], max_bytes: int, max_pages: int):
    sample_map = dict(samples)

    class H(BaseHTTPRequestHandler):
        server_version = "JSON9000"

        def log_message(self, fmt, *a):  # quiet; errors are logged by the worker
            log.debug(fmt, *a)

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8",
                       {"Cache-Control": "no-store"})

        def _err(self, code, msg):
            self._json({"error": msg}, code)

        def _body(self, limit=1 << 20) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                raise ValueError("too large")
            return self.rfile.read(n) if n else b""

        def _state(self):
            return {"doctypes": eng.describe(), "model": eng.model_name, "model_off": eng.model_off, "ocr": eng.ocr_engine,
                    "samples": [s for s, _ in samples],
                    "limits": {"max_mb": max_bytes // (1 << 20), "max_pages": max_pages}}

        def do_GET(self):
            u = urlparse(self.path)
            p = u.path
            if p in ("/", "/index.html"):
                return self._static("index.html")
            if p.startswith("/static/"):
                return self._static(p[len("/static/"):])
            if p == "/api/state":
                return self._json(self._state())
            m = re.fullmatch(r"/api/doctypes/([^/]+)/yaml", p)
            if m:
                c = next((c for c in eng.catalog if c["id"] == unquote(m.group(1))), None)
                if not c:
                    return self._err(404, "Nie ma takiego typu dokumentu.")
                return self._send(200, c["path"].read_bytes(), "text/plain; charset=utf-8")
            m = re.fullmatch(r"/api/jobs/(\w+)(?:/(page)/(\d+)\.jpg|/(output)\.json)?", p)
            if m:
                job = jobs.get(m.group(1))
                if not job:
                    return self._err(404, "Ten dokument nie jest już dostępny. Wgraj go ponownie.")
                if m.group(2):
                    n = int(m.group(3))
                    if not 0 <= n < job.n_pages:
                        return self._err(404, "Nie ma takiej strony.")
                    return self._send(200, render_page(job, n), "image/jpeg", {"Cache-Control": "private, max-age=3600"})
                if m.group(4):
                    if not job.view:
                        return self._err(409, "Dokument jest jeszcze przetwarzany.")
                    name = Path(job.name).with_suffix(".json").name
                    body = json.dumps(job.view["output"], ensure_ascii=False, indent=2).encode("utf-8")
                    return self._send(200, body, "application/json; charset=utf-8",
                                      {"Content-Disposition": f"attachment; filename*=UTF-8''{_q(name)}"})
                return self._json({"id": job.id, "name": job.name, "status": job.status, "stage": job.stage,
                                   "error": job.error, "pages": job.n_pages, "use_llm": job.use_llm,
                                   "view": job.view if job.status == "done" else None})
            return self._err(404, "Nie znaleziono.")

        def do_POST(self):
            p = urlparse(self.path).path
            qs = parse_qs(urlparse(self.path).query)
            use_llm = eng.llm is not None and qs.get("llm", ["1"])[0] == "1"
            try:
                if p == "/api/upload":
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > max_bytes:
                        return self._err(413, f"Plik jest za duży. Limit to {max_bytes // (1 << 20)} MB.")
                    data = self.rfile.read(n)
                    if not data.startswith(b"%PDF"):
                        return self._err(415, "To nie jest plik PDF.")
                    name = Path(unquote(self.headers.get("X-Filename") or "dokument.pdf")).name[:120] or "dokument.pdf"
                    dst = jobs.work_dir / f"{uuid.uuid4().hex}.pdf"
                    dst.write_bytes(data)
                    return self._new_job(name, dst, True, use_llm)
                if p == "/api/samples":
                    rel = json.loads(self._body() or b"{}").get("path")
                    if rel not in sample_map:
                        return self._err(404, "Nie ma takiego przykładu.")
                    return self._new_job(rel, sample_map[rel], False, use_llm)
                m = re.fullmatch(r"/api/jobs/(\w+)/rerun", p)
                if m:
                    job = jobs.get(m.group(1))
                    if not job:
                        return self._err(404, "Ten dokument nie jest już dostępny. Wgraj go ponownie.")
                    if job.status in ("queued", "running"):
                        return self._err(409, "Dokument jest jeszcze przetwarzany.")
                    jobs.rerun(job, use_llm)
                    return self._json({"id": job.id})
                if p == "/api/doctypes":
                    ids = json.loads(self._body() or b"{}").get("enabled", [])
                    problems = eng.apply([i for i in ids if isinstance(i, str)])
                    return self._json({"problems": problems, **self._state()}, 200 if not problems else 422)
                if p == "/api/doctypes/upload":
                    name = unquote(self.headers.get("X-Filename") or "typ.yaml")
                    problems = eng.add_uploaded(name, self._body(limit=256 << 10))
                    return self._json({"problems": problems, **self._state()}, 200 if not problems else 422)
                m = re.fullmatch(r"/api/doctypes/([^/]+)/remove", p)
                if m:
                    problems = eng.remove(unquote(m.group(1)))
                    return self._json({"problems": problems, **self._state()}, 200 if not problems else 422)
            except ValueError:
                return self._err(400, "Nieprawidłowe zapytanie.")
            return self._err(404, "Nie znaleziono.")

        def _new_job(self, name, path, owned, use_llm):
            try:
                with PDFIUM_LOCK:
                    pdf = pdfium.PdfDocument(str(path))
                    n = len(pdf)
                    pdf.close()
            except Exception:  # noqa: BLE001
                if owned:
                    path.unlink(missing_ok=True)
                return self._err(415, "Nie udało się otworzyć tego PDF-a. Plik może być uszkodzony albo zaszyfrowany.")
            if n > max_pages:
                if owned:
                    path.unlink(missing_ok=True)
                return self._err(413, f"Dokument ma {n} stron. Limit to {max_pages}.")
            job = jobs.add(name, path, owned, use_llm)
            return self._json({"id": job.id})

        def _static(self, rel: str):
            f = (STATIC / rel).resolve()
            if STATIC.resolve() not in f.parents or not f.is_file():
                return self._err(404, "Nie znaleziono.")
            cache = "no-cache" if f.suffix in (".html", ".js", ".css") else "public, max-age=86400"
            return self._send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"), {"Cache-Control": cache})

    return H


def _q(s: str) -> str:
    from urllib.parse import quote
    return quote(s)


def find_samples(folder: Path | None, limit: int = 300) -> list[tuple[str, Path]]:
    if not folder or not folder.is_dir():
        return []
    pdfs = sorted(p for p in folder.rglob("*") if p.suffix.lower() == ".pdf")[:limit]
    return [(str(p.relative_to(folder)).replace("\\", "/"), p) for p in pdfs]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="odwolania2json.ui", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to accept connections from other machines")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--config", type=Path, default=ROOT / "config")
    ap.add_argument("--doctype", dest="doctypes", type=Path, action="append", default=[],
                    help="enable an extra document-type YAML at start (more can be added in the browser)")
    ap.add_argument("--samples", type=Path, help="folder of PDFs offered as one-click examples")
    ap.add_argument("--no-llm", action="store_true", help="rules only")
    ap.add_argument("--no-ocr", action="store_true")
    ap.add_argument("--ocr-engine", choices=["mineru", "tesseract"])
    ap.add_argument("--max-mb", type=int, default=25)
    ap.add_argument("--max-pages", type=int, default=40)
    ap.add_argument("--work-dir", type=Path, help="where uploads are kept while the server runs (default: a temp folder)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    eng = Engine(args)
    work = args.work_dir or Path(tempfile.mkdtemp(prefix="json9000-ui-"))
    work.mkdir(parents=True, exist_ok=True)
    eng.added_dir = work / "doctypes"
    eng.added_dir.mkdir(exist_ok=True)
    jobs = Jobs(eng, work)
    samples = find_samples(args.samples)
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(eng, jobs, samples, args.max_mb << 20, args.max_pages))
    host = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host
    print(f"JSON9000 UI: http://{host}:{args.port}  | types: {', '.join(d.name for d in eng.doctypes)} | "
          f"OCR: {eng.ocr_engine or 'off'} | model: {eng.model_name or 'off (rules only)'} | samples: {len(samples)}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        if not args.work_dir:
            shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
