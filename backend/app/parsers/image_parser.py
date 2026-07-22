"""Image / OCR Agent (L2) — Tesseract via pytesseract, fully offline.

Covers JPEG/PNG/TIFF/BMP/WebP/HEIC. Confidence is reported per OCR block so
downstream agents can down-weight low-confidence extractions.
"""
import asyncio
import logging

import pytesseract
from PIL import Image

from app.config import get_settings
from app.models.schemas import FileCategory, ParsedDocument, TextBlock
from app.parsers.base import BaseParser

logger = logging.getLogger(__name__)


class ImageParser(BaseParser):
    category = FileCategory.IMAGE

    async def parse(self, file_path: str) -> ParsedDocument:
        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParsedDocument:
        settings = get_settings()
        doc = ParsedDocument(source_file=file_path, category=self.category)
        try:
            image = Image.open(file_path)
            image = image.convert("RGB")
            ocr_data = pytesseract.image_to_data(
                image, lang=settings.ocr_lang, output_type=pytesseract.Output.DICT
            )

            n = len(ocr_data["text"])
            line_buffer: dict[int, list[tuple[str, float]]] = {}
            for i in range(n):
                word = ocr_data["text"][i].strip()
                if not word:
                    continue
                conf = float(ocr_data["conf"][i]) if ocr_data["conf"][i] not in ("-1", -1) else 0.0
                line_key = (ocr_data["block_num"][i], ocr_data["par_num"][i], ocr_data["line_num"][i])
                line_buffer.setdefault(hash(line_key), []).append((word, conf))

            for words in line_buffer.values():
                text = " ".join(w for w, _ in words)
                avg_conf = sum(c for _, c in words) / len(words) / 100.0
                doc.text_blocks.append(TextBlock(text=text, kind="ocr", confidence=avg_conf))

            doc.metadata = {"width": image.width, "height": image.height, "parser": "tesseract"}
            if not doc.text_blocks:
                doc.warnings.append("No text detected by OCR.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("OCR failed on %s", file_path)
            doc.warnings.append(f"OCR error: {exc}")
        return doc
