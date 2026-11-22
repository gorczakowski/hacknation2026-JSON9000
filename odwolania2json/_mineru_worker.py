"""MinerU OCR of one PDF -> list of page texts (and, for the UI, the text blocks with their boxes).

Used in-process (when MinerU is installed in the same interpreter, e.g. the Docker image) or as a script in a
separate MinerU venv:  worker.py in.pdf out.json tier [layout]  -> writes {"pages": [...]} (+ "blocks": [...]).
"""
import json
import sys


def block_text(block):
    parts = []
    for c in block.get("content") or []:
        if isinstance(c, dict):
            if isinstance(c.get("content"), str):
                parts.append(c["content"])
            elif isinstance(c.get("content"), list):
                parts.append(block_text(c))
        elif isinstance(c, str):
            parts.append(c)
    return " ".join(p for p in parts if p)


def _middle_json(src: str, tier: str) -> dict:
    from mineru.parser import parse  # imported lazily: heavy, and optional outside the Docker image

    res = parse(src, tier=tier, ocr_mode="ocr", image_analysis=False)
    return res.middle_json if isinstance(res.middle_json, dict) else json.loads(res.to_json())


def _page_texts(mid: dict) -> list[str]:
    return ["\n".join(t for t in (block_text(b) for b in p.get("blocks", [])) if t.strip()) for p in mid.get("pages", [])]


def parse_pages(src: str, tier: str = "flash") -> list[str]:
    return _page_texts(_middle_json(src, tier))


def parse_layout(src: str, tier: str = "flash") -> tuple[list[str], list[list[dict]]]:
    """Same page texts as parse_pages, plus per page the blocks {"bbox": [x0, y0, x1, y1] (0-1, top-left origin),
    "text"} so the UI can point at where a value was read."""
    mid = _middle_json(src, tier)
    blocks = [[{"bbox": b.get("bbox"), "text": t} for b in p.get("blocks", []) if (t := block_text(b)).strip() and b.get("bbox")]
              for p in mid.get("pages", [])]
    return _page_texts(mid), blocks


def main():
    src, dst, tier = sys.argv[1], sys.argv[2], sys.argv[3]
    layout = len(sys.argv) > 4 and sys.argv[4] == "layout"
    if layout:
        pages, blocks = parse_layout(src, tier)
        out = {"pages": pages, "blocks": blocks}
    else:
        out = {"pages": parse_pages(src, tier)}
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
