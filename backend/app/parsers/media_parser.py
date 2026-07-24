"""Media Agent (L2) — audio/video, metadata-only in this build.

Full speech-to-text (Whisper) is deliberately NOT included here: it pulls
in another large torch-dependent model download on top of the
embedding/Docling ones this build already carries, and this session hit
repeated, hard-to-diagnose build failures getting the *existing* torch
dependency chain installed reliably on a constrained/corporate network.
Adding a second heavy model felt like the wrong tradeoff for this pass.

What this agent does today: confirms the file is readable, reports its
size, and clearly flags that transcription isn't performed, so the file
still gets a category, a slot in the pipeline, and an honest status
instead of being silently dropped. Swapping in real transcription later
is a contained change — see README's "Extending file type support".
"""
import asyncio
import logging
from pathlib import Path

from app.models.schemas import FileCategory, ParsedDocument, TextBlock
from app.parsers.base import BaseParser

logger = logging.getLogger(__name__)


class MediaParser(BaseParser):
    category = FileCategory.MEDIA

    async def parse(self, file_path: str) -> ParsedDocument:
        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParsedDocument:
        doc = ParsedDocument(source_file=file_path, category=self.category)
        try:
            size_bytes = Path(file_path).stat().st_size
            doc.text_blocks.append(TextBlock(
                text=f"Media file '{Path(file_path).name}' ({size_bytes / 1_048_576:.1f} MB) was received "
                     f"but not transcribed -- audio/video speech-to-text is not enabled in this deployment.",
                kind="paragraph",
            ))
            doc.metadata = {"size_bytes": size_bytes, "parser": "metadata-only", "transcribed": False}
            doc.warnings.append("Transcription not performed (Media Agent runs metadata-only in this build).")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Media parse failed on %s", file_path)
            doc.warnings.append(f"Media parse error: {exc}")
        return doc
