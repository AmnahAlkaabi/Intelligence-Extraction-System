"""Excel Agent (L2) — multi-sheet workbook ingest (openpyxl backend via pandas)."""
import asyncio
import logging

import pandas as pd

from app.models.schemas import FileCategory, ParsedDocument, TableBlock, TextBlock
from app.parsers.base import BaseParser

logger = logging.getLogger(__name__)

MAX_PREVIEW_ROWS = 500


class ExcelParser(BaseParser):
    category = FileCategory.EXCEL

    async def parse(self, file_path: str) -> ParsedDocument:
        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParsedDocument:
        doc = ParsedDocument(source_file=file_path, category=self.category)
        lower = file_path.lower()
        if lower.endswith(".ods"):
            # openpyxl/xlrd can't read OpenDocument spreadsheets at all
            # (that needs odfpy, not currently a dependency) -- previously
            # this fell through to openpyxl and failed with a cryptic
            # "not a zip file"/engine error; report the real reason instead.
            doc.warnings.append(
                "ODS (OpenDocument Spreadsheet) files aren't supported in this deployment -- "
                "convert to .xlsx first."
            )
            return doc
        # openpyxl only reads the modern .xlsx/.xlsm zip-based format; the
        # legacy binary .xls format needs xlrd instead (already a bundled
        # dependency for exactly this) -- forcing openpyxl on a .xls file
        # made it fail outright despite being an otherwise-valid workbook.
        engine = "xlrd" if lower.endswith(".xls") else "openpyxl"
        try:
            sheets = pd.read_excel(file_path, sheet_name=None, engine=engine)
            doc.text_blocks.append(
                TextBlock(text=f"Workbook contains {len(sheets)} sheet(s): {', '.join(sheets)}",
                          kind="paragraph")
            )
            for sheet_name, df in sheets.items():
                profile = [f"Sheet '{sheet_name}': {len(df)} rows x {len(df.columns)} cols"]
                for col in df.columns:
                    profile.append(f"  '{col}': dtype={df[col].dtype}, nulls={df[col].isna().mean()*100:.1f}%")
                doc.text_blocks.append(TextBlock(text="\n".join(profile), kind="paragraph"))

                preview = df.head(MAX_PREVIEW_ROWS).fillna("").astype(str)
                doc.tables.append(TableBlock(
                    sheet=sheet_name,
                    headers=list(preview.columns),
                    rows=preview.values.tolist(),
                    caption=sheet_name,
                ))
            doc.metadata = {"sheet_count": len(sheets), "parser": "openpyxl"}
        except Exception as exc:  # noqa: BLE001
            logger.exception("Excel parse failed on %s", file_path)
            doc.warnings.append(f"Excel parse error: {exc}")
        return doc
