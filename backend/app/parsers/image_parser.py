"""Image / OCR Agent (L2) — Tesseract via pytesseract, fully offline.

Covers JPEG/PNG/TIFF/BMP/WebP/HEIC. Confidence is reported per OCR block so
downstream agents can down-weight low-confidence extractions.
"""
import asyncio
import logging

from PIL import Image

from app.config import get_settings
from app.models.schemas import FileCategory, ParsedDocument
from app.parsers.base import BaseParser
from app.parsers.ocr_utils import ocr_image

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
            doc.text_blocks = ocr_image(image, settings.ocr_lang)
            doc.metadata = {"width": image.width, "height": image.height, "parser": "tesseract"}
            if not doc.text_blocks:
                doc.warnings.append("No text detected by OCR.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("OCR failed on %s", file_path)
            doc.warnings.append(f"OCR error: {exc}")
        return doc
