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

Docling's own per-page OCR trigger is a heuristic (it OCRs a page when
that page's native text coverage is below a threshold), which normally
covers scanned documents fine. But a PDF that's really just a photo or a
scan wrapped in a PDF container by a scanner driver -- one giant image
XObject, no meaningful text layer, sometimes an odd page-box/rotation --
can slip past that heuristic or trip up table/layout parsing entirely,
leaving the file with nothing extracted (score-tanking per
data_quality.py, and functionally useless to every downstream agent). If
Docling comes back with no text AND no tables, whether it raised or just
found nothing, this parser reroutes the whole file through the same
Tesseract OCR path the standalone Image specialist uses: render every
page to a bitmap (pypdfium2, already a docling dependency, so no new
package) and OCR it directly, rather than accepting an empty result for
a file that's clearly readable to a human.
"""
import asyncio
import logging

import pypdfium2 as pdfium
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

from app.config import get_settings
from app.models.schemas import FileCategory, ParsedDocument, TableBlock, TextBlock
from app.parsers.base import BaseParser
from app.parsers.ocr_utils import ocr_image

logger = logging.getLogger(__name__)

# ~144 DPI (scale is a multiple of the PDF's 72-DPI canvas unit) -- sharp
# enough for Tesseract on typical scanned-document text without ballooning
# render time/memory on a large page count.
_OCR_FALLBACK_SCALE = 2.0


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
        # DocumentConverter's constructor is a bare config shell — it does
        # NOT load model weights (see docling's own source: the pipeline,
        # and the HF Hub download inside it, is built lazily on first use).
        # Force that now, at parser construction time, rather than letting
        # it happen silently inside the first convert() call: a missing
        # local model cache then fails loudly right here (surfaced by the
        # startup preflight in main.py) instead of resurfacing as a
        # confusing live-network error on whichever upload happens to be
        # first, and every convert() call after this one skips the
        # one-time model-load cost instead of only the second PDF onward.
        self._converter.initialize_pipeline(InputFormat.PDF)

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
                            # Docling detected a table but its grid shape
                            # wasn't what we expected -- appending an empty
                            # TableBlock silently (the prior behavior) looks
                            # to the analyst like a genuinely empty table
                            # instead of a table that failed to parse.
                            logger.warning(
                                "Docling table grid unreadable on %s (page %s) -- table dropped",
                                file_path, page_no,
                            )
                            doc.warnings.append(
                                f"A table on page {page_no} could not be parsed and was skipped."
                                if page_no is not None else
                                "A table could not be parsed and was skipped."
                            )
                            continue
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

        if not doc.full_text().strip() and not doc.tables:
            self._ocr_fallback(file_path, doc)
        return doc

    def _ocr_fallback(self, file_path: str, doc: ParsedDocument) -> None:
        settings = get_settings()
        pdf = None
        try:
            pdf = pdfium.PdfDocument(file_path)
            for page_no, page in enumerate(pdf, start=1):
                bitmap = page.render(scale=_OCR_FALLBACK_SCALE)
                try:
                    image = bitmap.to_pil().convert("RGB")
                    doc.text_blocks.extend(ocr_image(image, settings.ocr_lang, page=page_no))
                finally:
                    bitmap.close()
                    page.close()
            if doc.text_blocks:
                doc.warnings.append(
                    "Docling found no text layer or tables in this PDF (likely a scan or "
                    "an image saved as PDF) — rerouted to page-image OCR."
                )
            else:
                doc.warnings.append(
                    "Docling found no text layer or tables, and the page-image OCR "
                    "fallback also found no readable text."
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("PDF page-image OCR fallback failed for %s", file_path)
            doc.warnings.append(f"PDF page-image OCR fallback error: {exc}")
        finally:
            if pdf is not None:
                pdf.close()
