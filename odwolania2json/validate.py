"""Config validation, run at startup (and by `--check-config`).

Checks every document-type YAML before a batch starts and reports problems as "<file>: <where>: <what>":
  - required keys (name, template, classify.rules, classify.threshold), unique type names
  - every regex compiles (classification rules, section boundaries, label/stop/regex/names_after patterns)
  - section references point at a defined section
  - output paths exist in the JSON template (fields, list paths, list item fields)
  - named post-processing steps (`derive`) exist
  - extraction methods use a known kind and value type (and valid `final` / `date_only_before` options)
"""

from __future__ import annotations

import re

from . import extract as X

METHOD_KINDS = {"label", "regex", "find", "place_date", "first_line", "names_after", "const", "nth"}
VALUE_TYPES = {"account", "amount", "date", "email", "token", "bzp", "ted", "text", "line"}
FIND_TYPES = {"email", "bzp", "ted", "date"}


class ConfigError(ValueError):
    pass


def _rx(errors, where, pattern):
    try:
        re.compile(X.fold(str(pattern)), re.I | re.M)
    except re.error as e:
        errors.append(f"{where}: invalid regex {pattern!r}: {e}")


def _has_path(tpl, path: str) -> bool:
    obj = tpl
    for k in path.split("."):
        if isinstance(obj, list):
            obj = obj[0] if obj else {}
        if not isinstance(obj, dict) or k not in obj:
            return False
        obj = obj[k]
    return True


def _item_template(tpl, path):
    obj = tpl
    for k in path.split("."):
        obj = obj[k]
    return obj[0] if isinstance(obj, list) and obj else None


def _methods(errors, where, methods, sections):
    if not isinstance(methods, list) or not methods:
        errors.append(f"{where}: expected a non-empty list of extraction methods")
        return
    for i, m in enumerate(methods):
        w = f"{where}[{i}]"
        if not isinstance(m, dict):
            errors.append(f"{w}: method must be a mapping")
            continue
        kinds = METHOD_KINDS & set(m)
        if len(kinds) != 1:
            errors.append(f"{w}: exactly one of {sorted(METHOD_KINDS)} expected, got {sorted(m)}")
            continue
        kind = kinds.pop()
        if "scope" in m and m["scope"] not in sections:
            errors.append(f"{w}: scope '{m['scope']}' is not a defined section ({sorted(sections) or 'none'})")
        t = m.get("type")
        if t and t not in VALUE_TYPES and not str(t).startswith("digits"):
            errors.append(f"{w}: unknown value type '{t}'")
        if kind in ("label", "names_after"):
            for lab in m[kind]:
                _rx(errors, f"{w}.{kind}", lab)
        if kind == "regex":
            _rx(errors, f"{w}.regex", m["regex"])
        if kind == "find" and m["find"] not in FIND_TYPES:
            errors.append(f"{w}: find '{m['find']}' not one of {sorted(FIND_TYPES)}")
        for st in m.get("stop", []):
            _rx(errors, f"{w}.stop", st)
        if "final" in m and not isinstance(m["final"], bool):
            errors.append(f"{w}: final must be true or false")
        if "date_only_before" in m:
            if kind != "place_date":
                errors.append(f"{w}: date_only_before only applies to place_date")
            _rx(errors, f"{w}.date_only_before", m["date_only_before"])


def validate(doctypes, derive_registry) -> list[str]:
    errors: list[str] = []
    seen = {}
    for dt in doctypes:
        f = str(dt.source or dt.name)
        cfg = dt.cfg
        for key in ("name", "classify"):
            if key not in cfg:
                errors.append(f"{f}: missing '{key}'")
        if "template" not in cfg and not isinstance(cfg.get("output"), dict):
            errors.append(f"{f}: needs either 'output:' (inline JSON shape) or 'template: file.json'")
        if dt.name in seen:
            errors.append(f"{f}: type name '{dt.name}' also defined in {seen[dt.name]}")
        seen[dt.name] = f
        c = cfg.get("classify") or {}
        if "threshold" not in c or not isinstance(c.get("rules"), list):
            errors.append(f"{f}: classify needs 'threshold' and a list of 'rules'")
        for i, r in enumerate(c.get("rules") or []):
            if "pattern" not in r or "weight" not in r:
                errors.append(f"{f}: classify.rules[{i}]: needs 'pattern' and 'weight'")
            else:
                _rx(errors, f"{f}: classify.rules[{i}]", r["pattern"])
        sections = cfg.get("sections") or {}
        for name, sd in sections.items():
            if not sd.get("start"):
                errors.append(f"{f}: sections.{name}: needs 'start'")
            for pat in (sd.get("start") or []) + (sd.get("end") or []):
                _rx(errors, f"{f}: sections.{name}", pat)
        for path, methods in (cfg.get("fields") or {}).items():
            if not _has_path(dt.template, path):
                errors.append(f"{f}: fields.{path}: path not in template {cfg.get('template')}")
            _methods(errors, f"{f}: fields.{path}", methods, sections)
        for path, ld in (cfg.get("lists") or {}).items():
            item = _item_template(dt.template, path) if _has_path(dt.template, path) else None
            if item is None:
                errors.append(f"{f}: lists.{path}: not a list in template {cfg.get('template')}")
                continue
            if ld.get("scope") and ld["scope"] not in sections:
                errors.append(f"{f}: lists.{path}.scope: '{ld['scope']}' is not a defined section")
            if ld.get("split"):
                _rx(errors, f"{f}: lists.{path}.split", ld["split"])
            if "from_names" in ld:
                _methods(errors, f"{f}: lists.{path}.from_names", ld["from_names"], sections)
                if not _has_path(item, ld.get("name_field", "")):
                    errors.append(f"{f}: lists.{path}.name_field: '{ld.get('name_field')}' not in list item")
            for fpath, methods in (ld.get("fields") or {}).items():
                if not _has_path(item, fpath):
                    errors.append(f"{f}: lists.{path}.fields.{fpath}: not in list item of the template")
                _methods(errors, f"{f}: lists.{path}.fields.{fpath}", methods, sections)
        for name in cfg.get("derive", []):
            if name not in derive_registry:
                errors.append(f"{f}: derive '{name}' unknown (available: {sorted(derive_registry)})")
        if "header_line" in cfg.get("derive", []):
            hl = cfg.get("header_line") or {}
            if not hl.get("prompt"):
                errors.append(f"{f}: header_line.prompt: needed by the header_line step")
            if not hl.get("date"):
                errors.append(f"{f}: header_line.date: needed by the header_line step")
            for key in ("date", "place"):
                if hl.get(key) and not _has_path(dt.template, hl[key]):
                    errors.append(f"{f}: header_line.{key}: path not in template {cfg.get('template')}")
        if "powod_and_date" in cfg.get("derive", []):
            pw = cfg.get("powod") or {}
            for i, r in enumerate(pw.get("rules", [])):
                _rx(errors, f"{f}: powod.rules[{i}]", r.get("pattern", ""))
                if r.get("id") not in (pw.get("labels") or {}):
                    errors.append(f"{f}: powod.rules[{i}]: id {r.get('id')} has no entry in powod.labels")
            ps = pw.get("passage") or {}
            for key in ("start", "fallback_start", "end"):
                for pat in ps.get(key, []):
                    _rx(errors, f"{f}: powod.passage.{key}", pat)
            if "first_item" in ps:
                _rx(errors, f"{f}: powod.passage.first_item", ps["first_item"])
    return errors
