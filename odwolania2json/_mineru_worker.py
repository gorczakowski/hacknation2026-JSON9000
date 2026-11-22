"""Runs inside the MinerU venv: OCR one PDF and write {"pages": [...]} as JSON.  Usage: worker.py in.pdf out.json tier"""
import json
import sys

from mineru.parser import parse


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


def main():
    src, dst, tier = sys.argv[1], sys.argv[2], sys.argv[3]
    res = parse(src, tier=tier, ocr_mode="ocr", image_analysis=False)
    mid = res.middle_json if isinstance(res.middle_json, dict) else json.loads(res.to_json())
    pages = ["\n".join(t for t in (block_text(b) for b in p.get("blocks", [])) if t.strip()) for p in mid.get("pages", [])]
    with open(dst, "w", encoding="utf-8") as f:
        json.dump({"pages": pages}, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
