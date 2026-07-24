"""Translator Agent -- language-detection + Qwen-based translation, run as
a preprocessing step between parsing and extraction so every downstream
agent (NER/PII/Financial/Relation, chunking/embedding) works on a single
consistent language regardless of source-document language.

Runs on the "translation" role (Qwen by default, same backend as
extraction) rather than a dedicated model -- per the confirmed decision to
keep the two-engine setup as-is instead of adding a third model.
"""
import logging

from langdetect import LangDetectException, detect

from app.config import get_settings
from app.llm.client import get_llm_client
from app.models.schemas import ParsedDocument

logger = logging.getLogger(__name__)

TRANSLATE_SYSTEM = (
    "You are a precise document translator. Translate the user's text to {target}. "
    "Preserve meaning, names, numbers, and structure exactly. Output ONLY the "
    "translation, no commentary, no original text."
)

# Batch text blocks into groups under this size per LLM call to keep calls
# fast, while still cutting the total call count well below one-per-block
# for documents with many short blocks.
_BATCH_CHARS = 4000
_BLOCK_SEP = "\n<<<BLOCK>>>\n"


def _detect_language(text: str) -> str | None:
    try:
        return detect(text)
    except LangDetectException:
        return None


async def translate_document(
    doc: ParsedDocument, unreachable_backends: set[str] | None = None
) -> ParsedDocument:
    """Mutates and returns doc. No-op when the text is already the target
    language, too short to reliably detect, or the translation backend is
    known unreachable (extraction then proceeds on the original-language
    text rather than blocking -- degraded, not stalled).
    """
    unreachable_backends = unreachable_backends or set()
    settings = get_settings()
    target = settings.translation_target_lang

    full_text = doc.full_text()
    if len(full_text.strip()) < settings.translation_min_chars:
        return doc

    lang = _detect_language(full_text[:3000])
    doc.detected_language = lang
    if lang is None or lang == target:
        return doc

    backend = get_llm_client().backend_for_role("translation")
    if backend in unreachable_backends:
        doc.warnings.append(
            f"Translation skipped: detected language '{lang}' but '{backend}' "
            f"model endpoint is unreachable -- extraction running on original-language text"
        )
        return doc

    blocks = [b for b in doc.text_blocks if b.text.strip()]
    if not blocks:
        return doc

    client = get_llm_client()
    system = TRANSLATE_SYSTEM.format(target=target)

    # Group blocks into batches under _BATCH_CHARS so long documents don't
    # need one LLM call per block, but a single oversized block still gets
    # its own call rather than being silently truncated.
    batches: list[list[int]] = []
    current: list[int] = []
    current_len = 0
    for i, b in enumerate(blocks):
        blen = len(b.text)
        if current and current_len + blen > _BATCH_CHARS:
            batches.append(current)
            current, current_len = [], 0
        current.append(i)
        current_len += blen
    if current:
        batches.append(current)

    translated_count = 0
    for batch in batches:
        joined = _BLOCK_SEP.join(blocks[i].text for i in batch)
        try:
            resp = await client.complete("translation", system, joined, max_tokens=4096)
        except Exception:
            logger.exception("Translation failed for a batch in %s", doc.source_file)
            doc.warnings.append(
                f"Translation failed for part of the document ({len(batch)} block(s)) -- "
                "left in original language"
            )
            continue

        parts = resp.text.split(_BLOCK_SEP)
        if len(parts) != len(batch):
            # Model didn't preserve the delimiter -- fall back rather than
            # mis-assigning fragments across unrelated blocks.
            doc.warnings.append(
                f"Translation batch delimiter mismatch in {doc.source_file} -- "
                "left original text for this batch"
            )
            continue

        for idx, translated_text in zip(batch, parts):
            blocks[idx].text = translated_text.strip()
            translated_count += 1

    if translated_count:
        doc.translated = True
        doc.metadata["translation_blocks"] = translated_count
        doc.metadata["translation_source_lang"] = lang
        # Not appended to doc.warnings: warnings feed DomainResult.errors,
        # which the Validator agent penalizes -- a successful translation
        # is not a quality issue.

    return doc
