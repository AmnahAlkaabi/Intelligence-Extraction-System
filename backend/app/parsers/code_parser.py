"""Code / Log Agent (L2) — plain-text source, logs, and config files
(.py, .js, .sql, .log, .md, .yaml, .toml, .txt).

Deliberately lightweight: reads as text and does a best-effort regex scan
for function/class definitions to give the downstream NER/summary agents
useful structure without pulling in a full parser (tree-sitter) per
language.
"""
import asyncio
import logging
import re

from app.models.schemas import FileCategory, ParsedDocument, TextBlock
from app.parsers.base import BaseParser

logger = logging.getLogger(__name__)

MAX_CHARS = 500_000  # guard against accidentally-huge log files
_DEF_PATTERN = re.compile(
    r"^\s*(def|class|function|const\s+\w+\s*=\s*(?:async\s+)?function|CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW|PROCEDURE|FUNCTION))\s+([A-Za-z_][\w.]*)",
    re.IGNORECASE | re.MULTILINE,
)


class CodeParser(BaseParser):
    category = FileCategory.CODE

    async def parse(self, file_path: str) -> ParsedDocument:
        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParsedDocument:
        doc = ParsedDocument(source_file=file_path, category=self.category)
        try:
            with open(file_path, encoding="utf-8", errors="replace") as f:
                text = f.read(MAX_CHARS + 1)
            truncated = len(text) > MAX_CHARS
            if truncated:
                text = text[:MAX_CHARS]
                doc.warnings.append(f"File truncated to {MAX_CHARS:,} characters for processing.")

            definitions = [m.group(2) for m in _DEF_PATTERN.finditer(text)][:100]
            if definitions:
                doc.text_blocks.append(TextBlock(
                    text="Detected definitions: " + ", ".join(definitions), kind="heading",
                ))

            # Paragraph-style chunking: blank-line-separated blocks, falling
            # back to fixed-size slices for logs with no natural breaks.
            paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
            if len(paragraphs) <= 1 and text.strip():
                lines = text.splitlines()
                paragraphs = ["\n".join(lines[i : i + 40]) for i in range(0, len(lines), 40)]

            for p in paragraphs:
                doc.text_blocks.append(TextBlock(text=p, kind="paragraph"))

            doc.metadata = {
                "char_count": len(text),
                "definition_count": len(definitions),
                "truncated": truncated,
                "parser": "plaintext",
            }
            if not doc.text_blocks:
                doc.warnings.append("File was empty or unreadable as text.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Code/log parse failed on %s", file_path)
            doc.warnings.append(f"Code/log parse error: {exc}")
        return doc
