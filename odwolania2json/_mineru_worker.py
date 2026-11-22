"""MinerU OCR of one PDF -> list of page texts.

Used in-process (when MinerU is installed in the same interpreter, e.g. the Docker image) or as a script in a
separate MinerU venv:  worker.py in.pdf out.json tier   -> writes {"pages": [...]}.
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


def parse_pages(src: str, tier: str = "flash") -> list[str]:
    from mineru.parser import parse  # imported lazily: heavy, and optional outside the Docker image

    res = parse(src, tier=tier, ocr_mode="ocr", image_analysis=False)
    mid = res.middle_json if isinstance(res.middle_json, dict) else json.loads(res.to_json())
    return ["\n".join(t for t in (block_text(b) for b in p.get("blocks", [])) if t.strip()) for p in mid.get("pages", [])]


def main():
    src, dst, tier = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(dst, "w", encoding="utf-8") as f:
        json.dump({"pages": parse_pages(src, tier)}, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
