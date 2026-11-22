"""Pipeline: read -> classify -> extract (rules from config) -> derive -> normalize -> JSON + evidence."""

from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import extract as X
from . import validators
from .reading import Document

log = logging.getLogger(__name__)


# --- config --------------------------------------------------------------------------

def strip_json_comments(text: str) -> str:
    out = []
    for line in text.splitlines():
        in_str = esc = False
        cut = len(line)
        for i, ch in enumerate(line):
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = not in_str
            elif not in_str and line.startswith("//", i):
                cut = i
                break
        out.append(line[:cut].rstrip())
    s = "\n".join(out)
    s = re.sub(r'("|\d|null|true|false|\}|\])(\s*\n\s*")', r"\1,\2", s)
    return re.sub(r",(\s*[}\]])", r"\1", s)


@dataclass
class DocType:
    name: str
    cfg: dict
    template: dict
    source: Path | None = None  # the YAML file this type came from (for error messages)
    template_text: str = ""  # the output shape as text (with comments) - used as instructions for the model


def _yaml_files(path: Path) -> list[Path]:
    return sorted(path.glob("*.yaml")) + sorted(path.glob("*.yml")) if path.is_dir() else [path]


def _template_for(cfg: dict, yaml_path: Path, config_dir: Path) -> Path:
    """Template lookup: next to the YAML, in its templates/ subfolder, then in the main config/templates."""
    name = cfg["template"]
    for cand in (yaml_path.parent / name, yaml_path.parent / "templates" / name, config_dir / "templates" / name):
        if cand.exists():
            return cand
    raise FileNotFoundError(f"{yaml_path}: template '{name}' not found next to the file or in {config_dir / 'templates'}")


def load_doctype(path: Path, config_dir: Path) -> DocType:
    """A document type is one YAML file. Its output shape is either written inline (`output:`, a single-file
    config) or kept in a separate JSON template (`template: name.json`, comments allowed)."""
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(cfg.get("output"), dict):
        return DocType(cfg["name"], cfg, cfg["output"], path, json.dumps(cfg["output"], ensure_ascii=False, indent=2))
    tpl = _template_for(cfg, path, config_dir)
    text = tpl.read_text(encoding="utf-8")
    return DocType(cfg["name"], cfg, json.loads(strip_json_comments(text)), path, text)


def load_config(config_dir: Path, extra: list[Path] | None = None, defaults: bool = True) -> tuple[dict, list[DocType]]:
    """Load pipeline settings plus document types.

    Default types come from <config>/doctypes/ (the three task types). `extra` adds more YAML files or folders
    (e.g. config/examples/orzeczenie_kio.yaml). A type with the same name as an earlier one replaces it.
    """
    pipeline = yaml.safe_load((config_dir / "pipeline.yaml").read_text(encoding="utf-8"))
    paths = (_yaml_files(config_dir / "doctypes") if defaults else []) + [f for e in (extra or []) for f in _yaml_files(e)]
    by_name: dict[str, DocType] = {}
    for p in paths:
        dt = load_doctype(p, config_dir)
        by_name[dt.name] = dt
    return pipeline, list(by_name.values())


# --- classification ----------------------------------------------------------------------

def classify(doc: Document, doctypes: list[DocType], head_chars: int = 4000) -> tuple[str | None, dict]:
    """Score every type; only types that reach their own threshold compete; best score wins.

    Ties between qualifying types are broken by `classify.priority` (higher wins); a remaining tie means
    'unknown' ({}) rather than an arbitrary pick.
    """
    head = X.fold(doc.norm[:head_chars])  # diacritics-insensitive: OCR often loses ł/ą/ę
    full = X.fold(doc.norm)
    scores = {}
    for dt in doctypes:
        c = dt.cfg["classify"]
        score = 0.0
        for r in c["rules"]:
            if re.search(X.fold(r["pattern"]), head if not r.get("anywhere") else full, re.I | re.M):
                score += r["weight"]
        scores[dt.name] = score
    qualified = [d for d in doctypes if scores[d.name] >= d.cfg["classify"]["threshold"]]
    if not qualified:
        return None, scores
    key = lambda d: (scores[d.name], d.cfg["classify"].get("priority", 0))
    qualified.sort(key=key, reverse=True)
    if len(qualified) > 1 and key(qualified[0]) == key(qualified[1]):
        return None, {**scores, "_tie": [d.name for d in qualified if key(d) == key(qualified[0])]}
    return qualified[0].name, scores


# --- extraction ------------------------------------------------------------------------------

def set_path(obj: dict, path: str, value):
    keys = path.split(".")
    for k in keys[:-1]:
        obj = obj[k]
    obj[keys[-1]] = value


def split_items(scope: X.Scope, rx: str | None, max_items: int) -> list[X.Scope]:
    if not rx:
        return [scope]
    starts = [m.start() for m in re.finditer(rx, scope.text, re.I | re.M)]
    if not starts:
        return [scope]
    items = []
    for i, s0 in enumerate(starts[:max_items]):
        s1 = starts[i + 1] if i + 1 < len(starts) else len(scope.text)
        items.append(X.Scope(scope.text[s0:s1], scope.offset + s0, scope.sections))
    return items


def extract_doc(doc: Document, dt: DocType, evidence: list) -> dict:
    cfg = dt.cfg
    text = doc.norm
    sections = X.build_sections(text, cfg.get("sections", {}))
    root = X.Scope(text, 0, sections)
    for sc in sections.values():
        sc.sections = sections
    out = copy.deepcopy(dt.template)
    _blank(out)

    for path, methods in (cfg.get("fields") or {}).items():
        h = X.run_field(methods, root)
        if h:
            set_path(out, path, h.value)
            evidence.append(_ev(path, h, text))

    for path, ldef in (cfg.get("lists") or {}).items():
        item_tpl = _get(dt.template, path)[0]
        scope = sections.get(ldef["scope"], root) if ldef.get("scope") else root
        if ldef.get("scope") and ldef["scope"] not in sections:
            set_path(out, path, [])
            continue
        items = []
        if "from_names" in ldef:  # one item per name found by a names_after method
            h = X.run_field(ldef["from_names"], scope)
            for j, n in enumerate((h.value if h else [])[: ldef.get("max_items", 10)]):
                it = copy.deepcopy(item_tpl)
                _blank(it)
                set_path(it, ldef["name_field"], n)
                for fpath, methods in (ldef.get("fields") or {}).items():
                    fh = X.run_field(methods, X.Scope(scope.text, scope.offset, sections))
                    if fh and j == 0:
                        set_path(it, fpath, fh.value)
                items.append(it)
                evidence.append(_ev(f"{path}[{j}].{ldef['name_field']}", h, text, value=n))
        else:
            for j, sc in enumerate(split_items(scope, ldef.get("split"), ldef.get("max_items", 5))):
                it = copy.deepcopy(item_tpl)
                _blank(it)
                found = False
                for fpath, methods in ldef["fields"].items():
                    h = X.run_field(methods, sc)
                    if h:
                        val = h.value
                        if isinstance(val, list) and "list_of" in (methods[0] if methods else {}):
                            val = [{methods[0]["list_of"]: v} for v in val]
                        set_path(it, fpath, val)
                        found = True
                        evidence.append(_ev(f"{path}[{j}].{fpath}", h, text))
                if found:
                    items.append(it)
        set_path(out, path, items)
    return out


def _get(obj, path):
    for k in path.split("."):
        obj = obj[k]
    return obj


def _blank(obj):
    """Set every leaf of the template to None and every list to []."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict):
                _blank(v)
            elif isinstance(v, list):
                obj[k] = []
            else:
                obj[k] = None


def _ev(path, h: X.Hit, text: str, value=None) -> dict:
    ctx = text[max(0, h.start - 40): h.end + 20] if h.start >= 0 else h.snippet
    return {"field": path, "value": h.value if value is None else value, "method": h.method,
            "start": h.start, "end": h.end, "context": re.sub(r"\s+", " ", ctx)[:200]}


# --- runner ----------------------------------------------------------------------------------

def process_document(doc: Document, doctypes: list[DocType], derive_fns: dict, head_chars: int = 4000,
                     llm=None, hybrid_cfg: dict | None = None):
    """Classify, extract with rules, let the model fill gaps / take the lead where configured, then derive."""
    from . import hybrid as H

    evidence: list = []
    hcfg = hybrid_cfg or {}
    doc.llm = llm if (llm is not None and llm.available) else None  # used by derive steps (e.g. 1-12 fallback)
    rules_type, scores = classify(doc, doctypes, head_chars)
    dtype, how = H.decide_type(doc, doctypes, rules_type, scores, doc.llm, hcfg, head_chars)
    meta = {"file": doc.path.name, "source": doc.source, "type": dtype, "type_decided_by": how,
            "class_scores": scores}
    if dtype is None:
        return {}, meta, evidence
    dt = next(d for d in doctypes if d.name == dtype)
    data = extract_doc(doc, dt, evidence)
    data, how_fields = H.model_fallback(doc, dt, data, doc.llm, hcfg, evidence)
    meta["fields_decided_by"] = how_fields
    for name in dt.cfg.get("derive", []):
        data = derive_fns[name](data, doc, dt, evidence)
    meta["checks"] = validators.check_all(data)
    return data, meta, evidence
