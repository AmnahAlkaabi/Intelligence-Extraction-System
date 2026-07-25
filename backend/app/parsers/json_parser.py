"""JSON / JSONL Agent (L2) — schema inference + nested path flattening."""
import asyncio
import json
import logging

from app.models.schemas import FileCategory, ParsedDocument, TableBlock, TextBlock
from app.parsers.base import BaseParser

logger = logging.getLogger(__name__)

MAX_PREVIEW_RECORDS = 200


def _flatten(obj, prefix: str = "", out: dict | None = None) -> dict:
    if out is None:
        out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten(v, f"{prefix}.{k}" if prefix else k, out)
    elif isinstance(obj, list):
        out[prefix] = f"[{len(obj)} items]"
        for i, item in enumerate(obj[:5]):
            _flatten(item, f"{prefix}[{i}]", out)
    else:
        out[prefix] = obj
    return out


class JSONParser(BaseParser):
    category = FileCategory.JSON_

    async def parse(self, file_path: str) -> ParsedDocument:
        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParsedDocument:
        doc = ParsedDocument(source_file=file_path, category=self.category)
        try:
            is_jsonl = file_path.lower().endswith(".jsonl")
            records: list = []
            if is_jsonl:
                # Each JSONL line is an independent record -- one malformed
                # line shouldn't discard every other (potentially valid)
                # record in the file the way letting the exception bubble
                # up to the outer try/except would.
                bad_lines = 0
                with open(file_path, encoding="utf-8") as f:
                    for lineno, line in enumerate(f, start=1):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError as exc:
                            bad_lines += 1
                            if bad_lines <= 10:
                                doc.warnings.append(f"JSONL line {lineno} skipped (invalid JSON): {exc}")
                if bad_lines > 10:
                    doc.warnings.append(f"...and {bad_lines - 10} more JSONL line(s) skipped (invalid JSON).")
            else:
                with open(file_path, encoding="utf-8") as f:
                    data = json.load(f)
                records = data if isinstance(data, list) else [data]

            key_freq: dict[str, int] = {}
            for rec in records[:MAX_PREVIEW_RECORDS]:
                flat = _flatten(rec)
                for k in flat:
                    key_freq[k] = key_freq.get(k, 0) + 1

            summary_lines = [f"Records: {len(records)}", "Key frequency (top-level flattened):"]
            for k, v in sorted(key_freq.items(), key=lambda kv: -kv[1])[:50]:
                summary_lines.append(f"  {k}: present in {v} records")
            doc.text_blocks.append(TextBlock(text="\n".join(summary_lines), kind="paragraph"))

            for rec in records[:MAX_PREVIEW_RECORDS]:
                doc.text_blocks.append(
                    TextBlock(text=json.dumps(rec, ensure_ascii=False, default=str)[:4000], kind="paragraph")
                )

            if records and isinstance(records[0], dict):
                headers = list(key_freq.keys())[:20]
                rows = []
                for rec in records[:MAX_PREVIEW_RECORDS]:
                    flat = _flatten(rec)
                    rows.append([str(flat.get(h, "")) for h in headers])
                doc.tables.append(TableBlock(headers=headers, rows=rows, caption="Flattened preview"))

            doc.metadata = {"record_count": len(records), "parser": "jsonl" if is_jsonl else "json"}
        except Exception as exc:  # noqa: BLE001
            logger.exception("JSON parse failed on %s", file_path)
            doc.warnings.append(f"JSON parse error: {exc}")
        return doc
