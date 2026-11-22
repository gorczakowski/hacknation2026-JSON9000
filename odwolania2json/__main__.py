"""CLI: python -m odwolania2json --input ./input --output ./output

For every PDF under --input writes <output>/<same relative path>.json (named after the PDF).
Documents that match no configured type get {}. Evidence (where each value came from) goes to
--evidence (default: <output>/../evidence), never next to the scored JSON files.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .derive import REGISTRY
from .pipeline import load_config, process_document
from .llm import LLM
from .reading import load_document, make_ocr
from .validate import validate
from .values import dump_output

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path):
    """Minimal .env loader (KEY=value lines); existing environment variables win."""
    import os
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="odwolania2json", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, default=Path("input"))
    ap.add_argument("--output", type=Path, default=Path("output"))
    ap.add_argument("--config", type=Path, default=ROOT / "config",
                    help="config folder: pipeline.yaml, doctypes/ (default types), templates/")
    ap.add_argument("--doctype", dest="doctypes", type=Path, action="append", default=[],
                    help="add a document-type YAML (or a folder of them), e.g. config/examples/orzeczenie_kio.yaml; repeatable")
    ap.add_argument("--no-default-doctypes", action="store_true", help="do not load <config>/doctypes/")
    ap.add_argument("--check-config", action="store_true", help="validate the configuration and exit")
    ap.add_argument("--strict", action="store_true", help="exit with code 2 if any document could not be read")
    ap.add_argument("--evidence", type=Path)
    ap.add_argument("--ocr-cache", type=Path, help="folder with cached OCR text (<rel>.txt) to skip running OCR")
    ap.add_argument("--no-ocr", action="store_true")
    ap.add_argument("--no-llm", action="store_true", help="rules only (no model calls)")
    ap.add_argument("--ocr-engine", choices=["mineru", "tesseract"], help="override ocr.engine from pipeline.yaml")
    ap.add_argument("--deadline", type=float, help="seconds; after that no OCR/model calls (default: runtime.deadline_s)")
    ap.add_argument("--workers", type=int, help="documents in parallel (default: runtime.workers)")
    ap.add_argument("--only", help="only PDFs whose relative path contains this string")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    pipeline_cfg, doctypes = load_config(args.config, args.doctypes, defaults=not args.no_default_doctypes)
    problems = validate(doctypes, REGISTRY)
    if problems:
        print("Configuration errors:", file=sys.stderr)
        for e in problems:
            print("  " + e, file=sys.stderr)
        return 3
    print("document types: " + ", ".join(f"{d.name} ({d.source.name if d.source else '?'})" for d in doctypes))
    if args.check_config:
        print("configuration OK")
        return 0
    load_env(ROOT / ".env")
    ocfg = dict(pipeline_cfg.get("ocr", {}) or {})
    if args.ocr_engine:
        ocfg["engine"] = args.ocr_engine
    if ocfg.get("cache_dir"):
        ocfg["cache_dir"] = str(ROOT / ocfg["cache_dir"])
    ocr = None if args.no_ocr else make_ocr(ocfg)
    lcfg = pipeline_cfg.get("llm", {}) or {}
    llm = LLM(lcfg) if (lcfg.get("enabled", True) and not args.no_llm) else None
    if llm is not None and not llm.available:
        print(f"WARNING: model fallback disabled - no API key in ${lcfg.get('api_key_env')} (.env)", file=sys.stderr)
        llm = None
    hcfg = pipeline_cfg.get("hybrid", {}) or {}
    rt = pipeline_cfg.get("runtime", {}) or {}
    deadline = args.deadline if args.deadline is not None else float(rt.get("deadline_s", 3000))
    workers = args.workers or int(rt.get("workers", 4))
    print(f"OCR: {'off' if ocr is None else ocfg.get('engine', 'mineru')} | model: "
          f"{'off (rules only)' if llm is None else lcfg.get('model') + ' @ ' + lcfg.get('base_url')} | workers: {workers}")
    evidence_root = args.evidence or args.output.parent / (args.output.name + "_evidence")
    pdfs = sorted(p for p in args.input.rglob("*") if p.suffix.lower() == ".pdf")
    if args.only:
        pdfs = [p for p in pdfs if args.only in str(p.relative_to(args.input)).replace("\\", "/")]
    head_chars = pipeline_cfg.get("classify", {}).get("head_chars", 4000)

    def run(pdf: Path) -> str:
        rel = pdf.relative_to(args.input)
        cached = (args.ocr_cache / rel).with_suffix(".txt") if args.ocr_cache else None
        # TIME GUARD - only a safety net against exceeding the evaluator's time limit (150 PDFs / 60 min).
        # It changes nothing for runs that finish within `runtime.deadline_s` (default 3000 s = 50 min): it is
        # only consulted when a run is still going after that point. Then remaining documents are processed with
        # rules only (no OCR, no model calls) so that every PDF still gets its output before the limit.
        late = time.time() - t0 > deadline
        doc = load_document(pdf, None if late else ocr, cached_ocr=cached)
        data, meta, evidence = process_document(doc, doctypes, REGISTRY, head_chars,
                                                llm=None if late else llm, hybrid_cfg=hcfg)
        meta["after_deadline"] = late
        if doc.error:
            meta["read_error"] = doc.error
            read_errors.append((str(rel), doc.error))
        out = (args.output / rel).with_suffix(".json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(dump_output(data), encoding="utf-8")
        ev = (evidence_root / rel).with_suffix(".json")
        ev.parent.mkdir(parents=True, exist_ok=True)
        ev.write_text(json.dumps({"meta": meta, "evidence": evidence}, ensure_ascii=False, indent=2), encoding="utf-8")
        if doc.error:
            return f"{rel} [{doc.source}] -> READ ERROR ({{}} written): {doc.error[:120]}"
        return f"{rel} [{doc.source}] -> {meta['type'] or 'nieznany typ ({})'}"

    t0 = time.time()
    # Hard stop for work already under way at the deadline: model retries and the MinerU subprocess end by
    # deadline + stop_grace_s (default 3 min, i.e. 53 min); the document then keeps its rules result.
    stop_at = t0 + deadline + float(rt.get("stop_grace_s", 180))
    if llm is not None:
        llm.stop_at = stop_at
    for o in (ocr, getattr(ocr, "primary", None), getattr(ocr, "fallback", None)):
        if o is not None and hasattr(o, "stop_at"):
            o.stop_at = stop_at
    failed = 0
    read_errors: list = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (pdf, fut) in enumerate(zip(pdfs, [ex.submit(run, p) for p in pdfs]), 1):
            try:
                print(f"[{i}/{len(pdfs)}] {fut.result()}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"[{i}/{len(pdfs)}] FAILED {pdf}: {e!r} - writing {{}}", file=sys.stderr)
                out = (args.output / pdf.relative_to(args.input)).with_suffix(".json")
                out.parent.mkdir(parents=True, exist_ok=True)
                if not out.exists():
                    out.write_text("{}", encoding="utf-8")  # every input PDF always gets an output file
    print(f"done: {len(pdfs)} files in {time.time() - t0:.1f}s, failed={failed}, read_errors={len(read_errors)}"
          + (f", model calls={llm.calls}, model failures={llm.failures}" if llm else ""))
    if read_errors:
        print(f"WARNING: {len(read_errors)} document(s) could not be read (OCR); they got {{}}:", file=sys.stderr)
        for rel, err in read_errors[:20]:
            print(f"  {rel}: {err[:200]}", file=sys.stderr)
    if failed:
        return 1
    return 2 if (read_errors and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
