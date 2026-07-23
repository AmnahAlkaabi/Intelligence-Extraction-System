"""PDF Agent (L2) — powered by Docling (github.com/DS4SD/docling).

Docling handles layout-aware text extraction and table structure
recognition (TableFormer) natively, which is why it's the primary parser
here rather than MinerU: for the business/legal/financial document types
this system targets, Docling's table fidelity and structured, reading-order
output win out, it's Apache-2.0 licensed, and it's meaningfully faster.
MinerU's edge (best-in-class layout mAP, LaTeX/formula recognition) mainly
pays off on multi-column scientific papers, which aren't the primary target
here — see README for how to add it as an alternate pipeline if needed.

For the OCR fallback on scanned pages, Docling is configured to use
RapidOCR rather than the Tesseract default. RapidOCR is an ONNX-runtime
deployment of PaddleOCR's detection/recognition/classification models —
same model family and accuracy as PaddleOCR, but without the heavy
PaddlePaddle framework dependency, and its default models ship bundled
inside the `rapidocr-onnxruntime` pip wheel itself. That means zero
runtime network calls even on a cold start on the air-gapped host: no
separate model-download step is needed the way Tesseract/EasyOCR require.
"""
import asyncio
import logging

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
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
        pipeline_options.ocr_options = RapidOcrOptions(text_score=settings.rapidocr_text_score)
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
