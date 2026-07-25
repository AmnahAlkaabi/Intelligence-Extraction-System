"""Email Agent (L2) — .eml (single message) and .mbox (message store) via
the standard library's email/mailbox modules only, no new dependencies.

.msg and .pst (Outlook's proprietary binary formats) are NOT handled here:
.msg would need the (non-stdlib) extract-msg package and .pst would need
libpst, neither of which are current build dependencies. Both are still
classified and routed so the analyst gets an honest "not supported"
warning instead of the file silently vanishing from the file list — see
README's "Extending file type support" for how to add real support later.

Attachment *content* is not extracted here (that would mean recursively
feeding each attachment back through the router — a bigger pipeline change
than this agent's scope); only filenames are listed, same trade-off the
Media Specialist makes for audio/video.
"""
import asyncio
import email
import logging
import mailbox
from email import policy
from email.message import EmailMessage

from bs4 import BeautifulSoup

from app.models.schemas import FileCategory, ParsedDocument, TableBlock, TextBlock
from app.parsers.base import BaseParser

logger = logging.getLogger(__name__)

# mbox files can hold many thousands of messages -- cap processing so one
# huge mailbox can't stall a job indefinitely (same rationale as
# extraction.py's MAX_SEGMENTS).
MAX_MESSAGES = 500
MAX_BODY_CHARS = 8000


def _plain_text_body(msg: EmailMessage) -> str:
    body = msg.get_body(preferencelist=("plain", "html"))
    if body is None:
        return ""
    content = body.get_content()
    if body.get_content_type() == "text/html":
        content = BeautifulSoup(content, "html.parser").get_text(separator="\n", strip=True)
    return content


def _attachment_names(msg: EmailMessage) -> list[str]:
    try:
        return [name for part in msg.iter_attachments() if (name := part.get_filename())]
    except Exception:  # noqa: BLE001 - a malformed MIME structure shouldn't drop the whole message
        return []


class EmailParser(BaseParser):
    category = FileCategory.EMAIL

    async def parse(self, file_path: str) -> ParsedDocument:
        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParsedDocument:
        doc = ParsedDocument(source_file=file_path, category=self.category)
        lower = file_path.lower()
        try:
            if lower.endswith((".msg", ".pst")):
                kind = "Outlook .msg message" if lower.endswith(".msg") else "Outlook .pst archive"
                doc.warnings.append(
                    f"{kind} files aren't supported in this deployment -- "
                    f"export to .eml/.mbox first, or convert with an external tool."
                )
                return doc

            is_mbox = lower.endswith(".mbox")
            if is_mbox:
                messages, skipped, truncated = self._load_mbox(file_path)
                if skipped:
                    doc.warnings.append(
                        f"{skipped} message(s) in the mailbox could not be parsed and were skipped."
                    )
            else:
                messages, truncated = self._load_eml(file_path), False
            if not messages:
                doc.warnings.append("No readable email message(s) found.")
                return doc

            rows = []
            for msg in messages:
                sender = msg.get("From", "")
                to = msg.get("To", "")
                cc = msg.get("Cc", "")
                subject = msg.get("Subject", "")
                date = msg.get("Date", "")
                attachments = _attachment_names(msg)

                try:
                    body = _plain_text_body(msg)[:MAX_BODY_CHARS]
                except Exception:
                    logger.exception("Failed to extract body for a message in %s", file_path)
                    body = ""
                    doc.warnings.append(f"A message's body could not be decoded (subject: {subject!r}).")

                header_text = f"From: {sender}\nTo: {to}"
                if cc:
                    header_text += f"\nCc: {cc}"
                header_text += f"\nSubject: {subject}\nDate: {date}"
                if attachments:
                    header_text += f"\nAttachments: {', '.join(attachments)}"
                doc.text_blocks.append(TextBlock(text=header_text, kind="heading"))
                if body.strip():
                    doc.text_blocks.append(TextBlock(text=body, kind="paragraph"))

                rows.append([sender, to, subject, date, str(len(attachments))])

            doc.tables.append(TableBlock(
                headers=["from", "to", "subject", "date", "attachment_count"],
                rows=rows,
                caption="Messages" if len(messages) > 1 else "Message",
            ))
            doc.metadata = {"message_count": len(messages), "parser": "mbox" if is_mbox else "eml"}
            if truncated:
                doc.warnings.append(
                    f"Mailbox contains more than {MAX_MESSAGES} messages -- "
                    f"only the first {MAX_MESSAGES} were processed."
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Email parse failed on %s", file_path)
            doc.warnings.append(f"Email parse error: {exc}")
        return doc

    def _load_eml(self, file_path: str) -> list[EmailMessage]:
        with open(file_path, "rb") as f:
            msg = email.message_from_binary_file(f, policy=policy.default)
        return [msg]

    def _load_mbox(self, file_path: str) -> tuple[list[EmailMessage], int, bool]:
        """Returns (messages, skipped_count, truncated). mbox.__getitem__ only
        swallows KeyError internally -- a single malformed message would
        otherwise raise straight out of iteration and abort every message
        after it, the same failure mode fixed for JSONL in json_parser.py.
        Each key is fetched individually here so one bad message is skipped
        instead of losing the rest of the mailbox."""
        box = mailbox.mbox(
            file_path,
            factory=lambda f: email.message_from_binary_file(f, policy=policy.default),
        )
        try:
            keys = box.keys()
            truncated = len(keys) > MAX_MESSAGES
            messages: list[EmailMessage] = []
            skipped = 0
            for key in keys[:MAX_MESSAGES]:
                try:
                    messages.append(box[key])
                except Exception:
                    logger.warning("Skipping unreadable message (key=%s) in %s", key, file_path)
                    skipped += 1
            return messages, skipped, truncated
        finally:
            box.close()
