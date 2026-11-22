"""Naive baseline: PDF -> (text layer | page images) -> LLM classify -> LLM extract -> JSON.

A reference point for the real pipeline, not the submission. It sends whole documents to
an OpenRouter model, which is NOT EU-compliant hosting. Synthetic data only.

Usage:
    python research/naive/naive.py --input hacknation-2026/dane --output out/naive [--limit 5] [--workers 8]

Config comes from .env (see .env.example): OPENROUTER_API_KEY, NAIVE_CLASSIFY_MODEL, NAIVE_EXTRACT_MODEL.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pypdfium2 as pdfium
from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[2]  # repo root
SPEC_DIR = ROOT / "hacknation-2026" / "oczekiwane-json"
DOC_TYPES = ["odwolanie", "pelnomocnictwo", "przelew"]
MIN_TEXT_CHARS = 200  # below this the PDF is treated as a scan and sent as images
MAX_IMAGE_PAGES = 4
RENDER_DPI = 150

POWOD = {
    1: "Decyzja o odrzuceniu oferty",
    2: "Ogłoszenie wyboru oferty",
    3: "Decyzja o wykluczeniu wykonawcy",
    4: "Publikacja specyfikacji warunków zamówienia (SWZ)",
    5: "Modyfikacja treści SWZ",
    6: "Odpowiedzi/wyjaśnienia do SWZ",
    7: "Zaniechanie odrzucenia oferty",
    8: "Zaniechanie wykluczenia wykonawcy",
    9: "Decyzja o unieważnieniu postępowania",
    10: "Zaniechanie unieważnienia postępowania",
    11: "Opis przedmiotu zamówienia (OPZ)",
    12: "Projektowane postanowienia umowy",
}

CLASSIFY_PROMPT = """Klasyfikujesz dokumenty ze sprawy odwoławczej przed Krajową Izbą Odwoławczą (KIO).
Typy:
- "odwolanie": odwołanie wnoszone przez wykonawcę (Odwołujący) do Prezesa KIO przeciwko czynnościom Zamawiającego.
- "pelnomocnictwo": dokument pełnomocnictwa, w którym mocodawca udziela pełnomocnictwa osobom.
- "przelew": potwierdzenie wykonania przelewu bankowego (np. wpis od odwołania).
- "inny": wszystko inne, m.in. wyroki i postanowienia KIO, ogłoszenia o zamówieniu (BZP, TED/Dz.U. UE), pisma urzędowe, broszury.
Zwróć JSON: {"typ": "<typ>"}."""

EXTRACT_PROMPT = """Wyodrębniasz dane z dokumentu typu "{doc_type}" i zwracasz WYŁĄCZNIE JSON
o dokładnie takiej strukturze jak wzór poniżej (te same klucze, ta sama kolejność, bez dodatkowych pól).
Komentarze // we wzorze opisują pola; wartości we wzorze to tylko przykłady, NIE kopiuj ich.

Zasady:
- Jeżeli danej wartości nie ma w dokumencie, podaj null. Nie zgaduj.
- Daty w formacie YYYY-MM-DD.
- Imiona i nazwiska w mianowniku, bez tytułów (r. pr., adw., mgr, dr itp.).
- NIP, REGON, KRS, PESEL, numery rachunków: same cyfry, jako string.
- Kwoty jako liczba (float).
- Listy mogą mieć więcej niż jeden element (np. konsorcjum, kilku pełnomocników).

Wzór:
{spec}"""


# --- spec / template handling ------------------------------------------------

def strip_json_comments(text: str) -> str:
    """Remove // comments outside of strings and repair the missing commas in the spec files."""
    out_lines = []
    for line in text.splitlines():
        in_str = False
        esc = False
        cut = len(line)
        for i, ch in enumerate(line):
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
            elif ch == '"':
                in_str = not in_str
            elif not in_str and line.startswith("//", i):
                cut = i
                break
        out_lines.append(line[:cut].rstrip())
    s = "\n".join(out_lines)
    # value followed by a new key on the next line without a comma
    s = re.sub(r'("|\d|null|true|false|\}|\])(\s*\n\s*")', r"\1,\2", s)
    s = re.sub(r",(\s*[}\]])", r"\1", s)
    return s


def load_specs() -> tuple[dict[str, str], dict[str, object]]:
    raw, skeleton = {}, {}
    for t in DOC_TYPES:
        text = (SPEC_DIR / f"{t}.json").read_text(encoding="utf-8")
        raw[t] = text
        skeleton[t] = json.loads(strip_json_comments(text))
    return raw, skeleton


def conform(value, template):
    """Force `value` into the shape of `template`: same keys/order, missing -> null, extra dropped."""
    if isinstance(template, dict):
        value = value if isinstance(value, dict) else {}
        return {k: conform(value.get(k), tv) for k, tv in template.items()}
    if isinstance(template, list):
        if not isinstance(value, list):
            return []
        return [conform(v, template[0]) for v in value if v is not None]
    return value  # leaf: keep whatever the model returned (normalized later)


def normalize(doc_type: str, data: dict) -> dict:
    digits = lambda v: re.sub(r"\D", "", str(v)) or None if v not in (None, "") else None
    if doc_type == "odwolanie":
        head = data["OdwolanieKIO"]
        try:
            pid = int(head["czynnosc_powod_id"]) if head["czynnosc_powod_id"] is not None else None
        except (TypeError, ValueError):
            pid = None
        head["czynnosc_powod_id"] = pid if pid in POWOD else None
        head["czynnosc_opis"] = POWOD.get(pid)
        for party in data["Odwolujacy"] + data["Zamawiajacy"]:
            for k in ("krs", "nip", "regon"):
                party[k] = digits(party[k])
        if len(data["Zamawiajacy"]) <= 1:  # only meaningful with several contracting authorities
            for z in data["Zamawiajacy"]:
                z["prowadzacy_postepowanie"] = None
        zp = data["ZamowieniePubliczne"]
        if zp["numer_bzp"]:
            zp["numer_bzp"] = re.sub(r"\s+", "", str(zp["numer_bzp"])).replace("bzp", "BZP")
        if zp["numer_ted"]:
            t = str(zp["numer_ted"])
            m = re.search(r"(\d{4})\s*/\s*S\s*\d+\s*-\s*(\d+)", t)  # 2026/S 137-999124 -> 999124-2026
            m2 = re.search(r"(\d{5,})\s*-\s*(\d{4})", t)
            zp["numer_ted"] = f"{m.group(2)}-{m.group(1)}" if m else (f"{m2.group(1)}-{m2.group(2)}" if m2 else t)
    elif doc_type == "pelnomocnictwo":
        for m in data["mocodawca"]:
            for k in ("krs", "nip"):
                m["data"][k] = digits(m["data"][k])
        for p in data["pelnomocnicy"]:
            p["pesel"] = digits(p["pesel"])
    elif doc_type == "przelew":
        pb = data["przelew_bankowy"]
        for k in ("na_rachunek", "z_rachunku"):
            pb[k] = digits(pb[k])
        if isinstance(pb["kwota"], str):
            s = pb["kwota"].replace("\xa0", "").replace(" ", "").replace("zł", "").replace("PLN", "")
            s = s.replace(".", "").replace(",", ".") if "," in s else s
            try:
                pb["kwota"] = float(s)
            except ValueError:
                pb["kwota"] = None
        elif isinstance(pb["kwota"], int):
            pb["kwota"] = float(pb["kwota"])
        if isinstance(pb["kwota"], float):
            pb["kwota"] = abs(pb["kwota"])  # debit shown as negative on some confirmations
    return data


# --- PDF reading -------------------------------------------------------------

PDFIUM_LOCK = threading.Lock()  # PDFium is not thread-safe


def read_pdf(path: Path) -> tuple[str, list[str]]:
    """Return (text, image_data_urls). Images only if the text layer is (nearly) empty."""
    with PDFIUM_LOCK:
        return _read_pdf(path)


def _read_pdf(path: Path) -> tuple[str, list[str]]:
    pdf = pdfium.PdfDocument(str(path))
    try:
        pages = [pdf[i].get_textpage().get_text_range() for i in range(len(pdf))]
        text = "\n\n".join(f"--- strona {i + 1} ---\n{t}" for i, t in enumerate(pages))
        if sum(len(t.strip()) for t in pages) >= MIN_TEXT_CHARS:
            return text, []
        images = []
        for i in range(min(len(pdf), MAX_IMAGE_PAGES)):
            img = pdf[i].render(scale=RENDER_DPI / 72).to_pil().convert("L")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            images.append("data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode())
        return "", images
    finally:
        pdf.close()


# --- LLM ---------------------------------------------------------------------

class LLM:
    def __init__(self):
        self.base_url = os.environ.get("NAIVE_BASE_URL", "https://openrouter.ai/api/v1")
        self.openrouter = "openrouter.ai" in self.base_url
        key = os.environ["OPENROUTER_API_KEY"] if self.openrouter else os.environ.get("NAIVE_API_KEY", "local")
        self.client = OpenAI(base_url=self.base_url, api_key=key, timeout=float(os.environ.get("NAIVE_TIMEOUT", "600")))
        self.lock = threading.Lock()
        self.tokens_in = self.tokens_out = 0
        self.cost = 0.0

    def json_call(self, model: str, system: str, text: str, images: list[str], schema: dict | None = None) -> dict:
        content: list[dict] = [{"type": "text", "text": text or "(dokument jest skanem, treść na obrazach)"}]
        content += [{"type": "image_url", "image_url": {"url": u}} for u in images]
        response_format = (
            {"type": "json_schema", "json_schema": {"name": "out", "strict": True, "schema": schema}}
            if schema else {"type": "json_object"}
        )
        if os.environ.get("NAIVE_NO_RESPONSE_FORMAT"):  # e.g. llama.cpp models whose grammar sampler fails
            response_format = None
        last_err = None
        for attempt in range(3):
            try:
                r = self.client.chat.completions.create(
                    model=model,
                    temperature=0,
                    max_tokens=int(os.environ.get("NAIVE_MAX_TOKENS", "4000")),
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
                    **({"response_format": response_format} if response_format else {}),
                    extra_body=({"provider": {"data_collection": "deny", "require_parameters": True},
                                 "usage": {"include": True},
                                 **({"reasoning": {"enabled": False}} if os.environ.get("NAIVE_REASONING") == "off" else {})}
                                if self.openrouter else (json.loads(os.environ["NAIVE_EXTRA_BODY"])
                                                         if os.environ.get("NAIVE_EXTRA_BODY") else None)),
                )
                with self.lock:
                    if r.usage:
                        self.tokens_in += r.usage.prompt_tokens or 0
                        self.tokens_out += r.usage.completion_tokens or 0
                        self.cost += float(getattr(r.usage, "cost", 0) or 0)
                raw = r.choices[0].message.content or ""
                raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
                if not raw.startswith("{"):  # free-form reply: take the outermost {...} block
                    a, b = raw.find("{"), raw.rfind("}")
                    raw = raw[a:b + 1] if a >= 0 and b > a else raw
                return json.loads(raw)
            except Exception as e:  # noqa: BLE001 - baseline, retry anything
                last_err = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"LLM call failed after retries: {last_err}")


# --- pipeline ----------------------------------------------------------------

def process(pdf_path: Path, in_root: Path, out_root: Path, llm: LLM, raw_specs, skeletons) -> str:
    rel = pdf_path.relative_to(in_root)
    out_path = (out_root / rel).with_suffix(".json")
    dbg_path = (out_root / "_debug" / rel).with_suffix(".json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dbg_path.parent.mkdir(parents=True, exist_ok=True)

    text, images = read_pdf(pdf_path)
    ocr_dir = os.environ.get("NAIVE_OCR_TEXT")
    if images and ocr_dir:  # scan: use cached OCR text instead of page images
        cached = (Path(ocr_dir) / pdf_path.relative_to(in_root)).with_suffix(".txt")
        if cached.exists() and cached.read_text(encoding="utf-8").strip():
            text, images = cached.read_text(encoding="utf-8"), []
    source = "image" if images else "text"
    classify_schema = {
        "type": "object",
        "properties": {"typ": {"type": "string", "enum": DOC_TYPES + ["inny"]}},
        "required": ["typ"],
        "additionalProperties": False,
    }
    # first ~2 pages are enough to decide the type
    cls_text = text[:6000]
    cls_images = images[:2]
    doc_type = llm.json_call(os.environ["NAIVE_CLASSIFY_MODEL"], CLASSIFY_PROMPT, cls_text, cls_images,
                             classify_schema).get("typ")

    debug = {"source": source, "type": doc_type}
    if doc_type not in DOC_TYPES:
        result = {}
    else:
        system = EXTRACT_PROMPT.format(doc_type=doc_type, spec=raw_specs[doc_type])
        raw = llm.json_call(os.environ["NAIVE_EXTRACT_MODEL"], system, text, images)
        debug["raw"] = raw
        result = normalize(doc_type, conform(raw, skeletons[doc_type]))

    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    dbg_path.write_text(json.dumps(debug, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"{rel}  [{source}] -> {doc_type}"


def main() -> int:
    load_dotenv(ROOT / "research" / ".env")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0, help="process at most N PDFs (0 = all)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--skip-existing", action="store_true")
    ap.add_argument("--model", help="override both NAIVE_CLASSIFY_MODEL and NAIVE_EXTRACT_MODEL")
    ap.add_argument("--ocr-text", help="folder with cached OCR text; scans use it instead of page images")
    ap.add_argument("--only", help="only PDFs whose relative path contains this string")
    args = ap.parse_args()

    if not os.environ.get("OPENROUTER_API_KEY") and not os.environ.get("NAIVE_BASE_URL"):
        print("OPENROUTER_API_KEY missing - copy .env.example to .env and fill it in", file=sys.stderr)
        return 2
    if args.ocr_text:
        os.environ["NAIVE_OCR_TEXT"] = str(Path(args.ocr_text).resolve())
    if args.model:
        os.environ["NAIVE_CLASSIFY_MODEL"] = os.environ["NAIVE_EXTRACT_MODEL"] = args.model
    os.environ.setdefault("NAIVE_CLASSIFY_MODEL", "mistralai/mistral-small-3.2-24b-instruct")
    os.environ.setdefault("NAIVE_EXTRACT_MODEL", "mistralai/mistral-small-3.2-24b-instruct")

    in_root, out_root = args.input.resolve(), args.output.resolve()
    pdfs = sorted(p for p in in_root.rglob("*") if p.suffix.lower() == ".pdf")
    if args.skip_existing:
        pdfs = [p for p in pdfs if not (out_root / p.relative_to(in_root)).with_suffix(".json").exists()]
    if args.only:
        pdfs = [p for p in pdfs if args.only in str(p.relative_to(in_root)).replace(chr(92), "/")]
    if args.limit:
        pdfs = pdfs[: args.limit]
    print(f"{len(pdfs)} PDFs | classify={os.environ['NAIVE_CLASSIFY_MODEL']} "
          f"extract={os.environ['NAIVE_EXTRACT_MODEL']}")

    raw_specs, skeletons = load_specs()
    llm = LLM()
    t0 = time.time()
    failed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(process, p, in_root, out_root, llm, raw_specs, skeletons): p for p in pdfs}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                print(f"[{i}/{len(pdfs)}] {f.result()}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"[{i}/{len(pdfs)}] FAILED {futs[f].relative_to(in_root)}: {e}", file=sys.stderr)
    print(f"done in {time.time() - t0:.0f}s, failed={failed}, tokens in={llm.tokens_in} out={llm.tokens_out}, "
          f"cost=${llm.cost:.4f}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
