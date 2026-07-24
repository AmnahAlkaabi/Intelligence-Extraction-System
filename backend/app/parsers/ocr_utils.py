"""Shared Tesseract OCR helper.

Used by both the standalone Image specialist (image_parser.py) and the
PDF-image-fallback path in pdf_parser.py, so the word/line grouping logic
that turns pytesseract's flat per-word output into TextBlocks lives in
exactly one place instead of being duplicated between the two.
"""
from PIL import Image

import pytesseract

from app.models.schemas import TextBlock


def ocr_image(image: Image.Image, lang: str, page: int | None = None) -> list[TextBlock]:
    ocr_data = pytesseract.image_to_data(image, lang=lang, output_type=pytesseract.Output.DICT)

    n = len(ocr_data["text"])
    line_buffer: dict[int, list[tuple[str, float]]] = {}
    for i in range(n):
        word = ocr_data["text"][i].strip()
        if not word:
            continue
        conf = float(ocr_data["conf"][i]) if ocr_data["conf"][i] not in ("-1", -1) else 0.0
        line_key = (ocr_data["block_num"][i], ocr_data["par_num"][i], ocr_data["line_num"][i])
        line_buffer.setdefault(hash(line_key), []).append((word, conf))

    blocks = []
    for words in line_buffer.values():
        text = " ".join(w for w, _ in words)
        avg_conf = sum(c for _, c in words) / len(words) / 100.0
        blocks.append(TextBlock(text=text, kind="ocr", confidence=avg_conf, page=page))
    return blocks
