"""PDF Agent (L2) — powered by Docling (github.com/DS4SD/docling).

Docling handles layout-aware text extraction, table structure recognition
and OCR fallback for scanned pages, all locally (its models are downloaded
once and cached — no runtime network dependency once cached on the
air-gapped host).
"""
import asyncio
import logging

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

from app.config import get_settings
from app.models.schemas import FileCategory, ParsedDocument, TableBlock, TextBlock
from app.parsers.base import BaseParser

logger = logging.getLogger(__name__)


class PDFParser(BaseParser):
    category = FileCategory.PDF

    def __init__(self) -> None:
        settings = get_settings()
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = settings.docling_do_ocr
        pipeline_options.do_table_structure = settings.docling_do_table_structure
        self._converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )

    async def parse(self, file_path: str) -> ParsedDocument:
        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParsedDocument:
        doc = ParsedDocument(source_file=file_path, category=self.category)
        try:
            result = self._converter.convert(file_path)
            dl_doc = result.document

            for item, _level in dl_doc.iterate_items():
                label = getattr(item, "label", "") or ""
                text = getattr(item, "text", None)
                page_no = None
                prov = getattr(item, "prov", None)
                if prov:
                    try:
                        page_no = prov[0].page_no
                    except (IndexError, AttributeError):
                        page_no = None

                if label == "table":
                    table_data = getattr(item, "data", None)
                    headers, rows = [], []
                    if table_data is not None:
                        try:
                            grid = table_data.grid
                            if grid:
                                headers = [c.text for c in grid[0]]
                                rows = [[c.text for c in r] for r in grid[1:]]
                        except AttributeError:
                            pass
                    doc.tables.append(TableBlock(page=page_no, headers=headers, rows=rows))
                elif text:
                    kind = "heading" if label in ("title", "section_header") else "paragraph"
                    doc.text_blocks.append(TextBlock(text=text, page=page_no, kind=kind))

            doc.metadata = {
                "page_count": len(dl_doc.pages) if hasattr(dl_doc, "pages") else None,
                "parser": "docling",
            }
        except Exception as exc:  # noqa: BLE001 - surface as a warning, never crash the pipeline
            logger.exception("Docling failed on %s", file_path)
            doc.warnings.append(f"Docling parse error: {exc}")
        return doc
