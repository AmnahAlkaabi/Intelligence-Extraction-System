"""JSON / JSONL Agent (L2) — schema inference + nested path flattening."""
import asyncio
import json
import logging
from collections import Counter

import ijson

from app.models.schemas import FileCategory, ParsedDocument, TableBlock, TextBlock
from app.parsers.base import BaseParser

logger = logging.getLogger(__name__)

# Records past this cap still count towards record_count and the "Key
# frequency" schema summary (see below), but never get their own
# individual text_block (the per-record dump NER/PII/Financial extraction
# reads via doc.full_text()) or a row in the small on-screen preview
# table -- dumping/embedding hundreds of thousands of individual records
# as separate LLM-sized text blocks would be prohibitively expensive, and
# no sibling parser (CSV/Excel) attempts per-row extraction at all.
MAX_PREVIEW_RECORDS = 200

# Ceiling for the *full* (uncapped) copy that feeds the structured-query
# SQL store (see storage/structured_store.py) -- mirrors
# csv_parser.MAX_STRUCTURED_ROWS/excel_parser.MAX_STRUCTURED_ROWS. Much
# larger than the on-screen preview, but still bounded so an accidentally
# huge upload can't blow up memory; when even this is exceeded, a warning
# says so rather than silently dropping the tail (same convention as
# CSV/Excel).
MAX_STRUCTURED_RECORDS = 200_000

# How many items of a nested list get their own flattened path (e.g.
# "transactions[0].amount") before the rest collapse into just the
# "[N items]" count. Raised from the previous hard cap of 5 so a record
# with, say, 50 nested transactions surfaces meaningfully more of them in
# the flattened preview/table instead of only the first handful.
MAX_NESTED_LIST_ITEMS = 20

# Recursion-depth ceiling for _flatten, independent of (and much lower
# than) Python's own default recursion limit (1000). A pathologically
# deep-but-tiny JSON document (a few KB of `[[[[...]]]]`) parses fine via
# ijson/json.load (see _parse_sync) but would blow Python's call stack
# inside a naive recursive flattener -- surfacing as a confusing
# "JSON parse error: maximum recursion depth exceeded" with no indication
# of what actually went wrong. Stopping well short of that limit here
# means a too-deep branch degrades to a single truncated JSON-string
# value instead of crashing the whole file's parse.
MAX_FLATTEN_DEPTH = 50


def _flatten(obj, prefix: str = "", out: dict | None = None, depth: int = 0) -> dict:
    if out is None:
        out = {}
    if depth >= MAX_FLATTEN_DEPTH:
        out[prefix] = json.dumps(obj, ensure_ascii=False, default=str)[:500] \
            if isinstance(obj, (dict, list)) else obj
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten(v, f"{prefix}.{k}" if prefix else k, out, depth + 1)
    elif isinstance(obj, list):
        shown = min(len(obj), MAX_NESTED_LIST_ITEMS)
        out[prefix] = (
            f"[{len(obj)} items]" if shown == len(obj)
            else f"[{len(obj)} items, first {shown} shown]"
        )
        for i, item in enumerate(obj[:MAX_NESTED_LIST_ITEMS]):
            _flatten(item, f"{prefix}[{i}]", out, depth + 1)
    else:
        out[prefix] = obj
    return out


def _flatten_record(rec) -> dict:
    # Top-level dict records keep their existing flattened key names
    # ("name", "address.city", ...) unchanged. A top-level record that
    # ISN'T a dict (a bare scalar like "active", or a list like [1, 2]) has
    # no natural key of its own -- without a synthetic "value" prefix,
    # _flatten would key it under "" (a scalar) or "[0]"/"[1]" (a list),
    # which produced no usable table at all under the old dict-only check.
    prefix = "" if isinstance(rec, dict) else "value"
    return _flatten(rec, prefix)


def _open_stripped_of_bom(file_path: str):
    """Binary file handle positioned just past a leading UTF-8 BOM, if
    present. ijson reads raw bytes -- handing it a text-mode reader works
    but triggers a deprecation warning and extra encode/decode overhead on
    every chunk, so the BOM (common in Windows-exported JSON) is stripped
    at the byte level here instead of via a text-decoding codec."""
    f = open(file_path, "rb")
    if f.read(3) != b"\xef\xbb\xbf":
        f.seek(0)
    return f


def _make_duplicate_key_tracker() -> tuple[type, list[str]]:
    """Returns a dict subclass (for ijson's map_type / a json
    object_pairs_hook, both of which build every JSON object encountered
    -- at any nesting depth -- through this type instead of a plain
    dict) plus the list it records into.

    RFC 8259 explicitly leaves duplicate object keys implementation-
    defined; Python's json module and ijson both silently keep only the
    LAST value for a repeated key, exactly like a `{...}` dict literal
    would. From the uploader's point of view that's real, silent data
    loss -- their first value for that key is just gone, with nothing in
    the response indicating it ever existed. This surfaces it as a
    warning instead.

    A fresh class+list pair is created per parse call (not shared module
    state) so concurrent files parsing on different threads
    (asyncio.to_thread) never cross-contaminate each other's counts."""
    duplicate_keys: list[str] = []

    class _DuplicateTrackingDict(dict):
        def __setitem__(self, key, value):
            if key in self:
                duplicate_keys.append(key)
            super().__setitem__(key, value)

    return _DuplicateTrackingDict, duplicate_keys


def _pairs_hook_for(dict_type: type):
    """Adapts a map_type-style dict subclass (built via repeated
    __setitem__ calls, what ijson uses) into the object_pairs_hook shape
    json.load/json.loads expects instead (a single function receiving
    all of an object's (key, value) pairs at once) -- routing both
    through the same __setitem__ override keeps duplicate detection
    identical between the streamed-array path (ijson) and the
    single-object/JSONL paths (stdlib json)."""
    def hook(pairs):
        d = dict_type()
        for k, v in pairs:
            d[k] = v
        return d
    return hook


def _duplicate_key_warning(duplicate_keys: list[str]) -> str | None:
    if not duplicate_keys:
        return None
    counts = Counter(duplicate_keys)
    examples = ", ".join(f"'{k}'" for k in list(counts)[:10])
    more = f" (and {len(counts) - 10} more distinct key name(s))" if len(counts) > 10 else ""
    return (
        f"Found {len(duplicate_keys)} duplicate object key occurrence(s) in this file "
        f"(e.g. {examples}{more}) -- for each, only the LAST value was kept; the earlier "
        f"value(s) were silently discarded, which is standard (if surprising) JSON parsing "
        f"behavior, not a bug in this parser."
    )


def _peek_first_char(file_path: str) -> str:
    """First non-whitespace character of the file -- used only to decide
    whether the top-level JSON value is an array (stream it) or a single
    object/scalar (just load it), without reading the whole file into
    memory to find out."""
    with _open_stripped_of_bom(file_path) as f:
        while True:
            chunk = f.read(4096)
            if not chunk:
                return ""
            chunk = chunk.lstrip()
            if chunk:
                return chr(chunk[0])


class JSONParser(BaseParser):
    category = FileCategory.JSON_

    async def parse(self, file_path: str) -> ParsedDocument:
        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParsedDocument:
        doc = ParsedDocument(source_file=file_path, category=self.category)
        try:
            is_jsonl = file_path.lower().endswith(".jsonl")
            records: list = []
            record_count = 0
            dup_dict_type, duplicate_keys = _make_duplicate_key_tracker()
            pairs_hook = _pairs_hook_for(dup_dict_type)
            if is_jsonl:
                # Each JSONL line is an independent record -- one malformed
                # line shouldn't discard every other (potentially valid)
                # record in the file the way letting the exception bubble
                # up to the outer try/except would. Lines are read one at a
                # time (already streaming, unlike a single json.load() of a
                # whole top-level array), but records beyond
                # MAX_STRUCTURED_RECORDS are counted and discarded rather
                # than kept, so a huge JSONL upload doesn't hold every
                # record in memory just to be counted.
                bad_lines = 0
                # utf-8-sig: strips a leading UTF-8 BOM if present (common
                # in Windows-exported JSON/JSONL) and behaves exactly like
                # plain utf-8 when there is none -- strict "utf-8" instead
                # rejects the whole file over a single leading BOM byte.
                with open(file_path, encoding="utf-8-sig") as f:
                    for lineno, line in enumerate(f, start=1):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line, object_pairs_hook=pairs_hook)
                        except json.JSONDecodeError as exc:
                            bad_lines += 1
                            if bad_lines <= 10:
                                doc.warnings.append(f"JSONL line {lineno} skipped (invalid JSON): {exc}")
                            continue
                        except RecursionError:
                            bad_lines += 1
                            if bad_lines <= 10:
                                doc.warnings.append(f"JSONL line {lineno} skipped (nested too deeply)")
                            continue
                        record_count += 1
                        if len(records) < MAX_STRUCTURED_RECORDS:
                            records.append(rec)
                if bad_lines > 10:
                    doc.warnings.append(f"...and {bad_lines - 10} more JSONL line(s) skipped (invalid JSON).")
            else:
                is_array = _peek_first_char(file_path) == "["
                if is_array:
                    # Stream the top-level array item by item instead of
                    # materializing the whole file with one json.load()
                    # call -- a very large upload (hundreds of thousands of
                    # records) no longer has to fit entirely in memory just
                    # to be parsed. Records beyond MAX_STRUCTURED_RECORDS
                    # are still counted (record_count stays accurate for
                    # the warning below) but not retained.
                    with _open_stripped_of_bom(file_path) as f:
                        for rec in ijson.items(f, "item", map_type=dup_dict_type):
                            record_count += 1
                            if len(records) < MAX_STRUCTURED_RECORDS:
                                records.append(rec)
                else:
                    with open(file_path, encoding="utf-8-sig") as f:
                        data = json.load(f, object_pairs_hook=pairs_hook)
                    records = [data]
                    record_count = 1

            key_freq: dict[str, int] = {}
            flat_records: list[dict] = []
            for rec in records:
                flat = _flatten_record(rec)
                flat_records.append(flat)
                for k in flat:
                    key_freq[k] = key_freq.get(k, 0) + 1

            summary_lines = [f"Records: {record_count}", "Key frequency (top-level flattened):"]
            for k, v in sorted(key_freq.items(), key=lambda kv: -kv[1])[:50]:
                summary_lines.append(f"  {k}: present in {v} records")
            doc.text_blocks.append(TextBlock(text="\n".join(summary_lines), kind="paragraph"))

            for rec in records[:MAX_PREVIEW_RECORDS]:
                doc.text_blocks.append(
                    TextBlock(text=json.dumps(rec, ensure_ascii=False, default=str)[:4000], kind="paragraph")
                )

            if records:
                headers = list(key_freq.keys())[:20]
                preview_rows = [
                    [str(flat.get(h, "")) for h in headers]
                    for flat in flat_records[:MAX_PREVIEW_RECORDS]
                ]
                doc.tables.append(TableBlock(
                    headers=headers, rows=preview_rows,
                    caption="Preview" if record_count > MAX_PREVIEW_RECORDS else "Full data",
                ))
                # Same "capped preview vs uncapped structured copy" split
                # CSV/Excel use: doc.tables above stays small for the Data
                # Dump tab/export, doc.full_tables feeds
                # storage/structured_store.py so chat's text-to-SQL branch
                # sees every loaded record, not just the on-screen preview.
                if record_count > MAX_PREVIEW_RECORDS:
                    full_rows = [[str(flat.get(h, "")) for h in headers] for flat in flat_records]
                    doc.full_tables.append(TableBlock(
                        headers=headers, rows=full_rows,
                        caption=f"Full data ({len(full_rows)} of {record_count} records)",
                    ))
                    if record_count > MAX_STRUCTURED_RECORDS:
                        doc.warnings.append(
                            f"Structured-query store only indexed the first {MAX_STRUCTURED_RECORDS} of "
                            f"{record_count} records -- aggregate SQL answers over this file may be incomplete."
                        )

            dup_warning = _duplicate_key_warning(duplicate_keys)
            if dup_warning:
                doc.warnings.append(dup_warning)

            doc.metadata = {"record_count": record_count, "parser": "jsonl" if is_jsonl else "json"}
        except RecursionError:
            logger.exception("JSON parse failed on %s (nested too deeply)", file_path)
            doc.warnings.append(
                "JSON parse error: file is nested too deeply to parse safely -- reduce nesting depth "
                "or flatten the structure before re-uploading."
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("JSON parse failed on %s", file_path)
            doc.warnings.append(f"JSON parse error: {exc}")
        return doc
