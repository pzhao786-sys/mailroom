"""Turn raw mail (an .eml file, or a JSON fixture used by evals) into an InboundEmail with
attachment text extracted. Gmail and IMAP connectors produce the same object."""

from __future__ import annotations

import email
import email.policy
import hashlib
import io
from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any

from ..domain import Attachment, InboundEmail

MAX_ATTACHMENT_CHARS = 6000
_TEXT_TYPES = {"text/plain", "text/csv", "text/markdown"}


def extract_attachment(filename: str, data: bytes, content_type: str | None = None) -> Attachment:
    ctype = content_type or _guess_type(filename)
    att = Attachment(filename=filename, content_type=ctype, size_bytes=len(data))
    try:
        if ctype == "application/pdf":
            text = _pdf_text(data)
        elif ctype in _TEXT_TYPES:
            text = data.decode("utf-8", errors="replace")
        else:
            att.readable, att.note = False, f"{ctype} is not supported for text extraction (no OCR configured)"
            return att
    except Exception as exc:  # corrupt files must not stop the pipeline
        att.readable, att.note = False, f"could not read: {type(exc).__name__}"
        return att
    text = text.strip()
    if len(text) < 20:
        att.readable, att.note = False, "no text layer (likely a scanned image; no OCR configured)"
        return att
    if len(text) > MAX_ATTACHMENT_CHARS:
        text, att.note = text[:MAX_ATTACHMENT_CHARS], f"truncated to {MAX_ATTACHMENT_CHARS} characters"
    att.text = text
    return att


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _guess_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "pdf": "application/pdf", "txt": "text/plain", "csv": "text/csv", "md": "text/markdown",
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    }.get(ext, "application/octet-stream")


def from_fixture(data: dict[str, Any], attachments_dir: Path | None = None, *, fallback_id: str | None = None) -> InboundEmail:
    """Fixture format (evals and demos):
    {"from": "...", "name": "...", "to": [...], "subject": "...", "body": "...",
     "received_at": "2026-11-18T09:12:00-06:00", "attachments": ["file.pdf"], "message_id": optional}
    """
    attachments = []
    for name in data.get("attachments", []):
        path = (attachments_dir or Path(".")) / name
        attachments.append(extract_attachment(path.name, path.read_bytes()))
    message_id = data.get("message_id") or fallback_id or _stable_id(data)
    return InboundEmail(
        message_id=message_id,
        thread_id=data.get("thread_id"),
        sender=data["from"].strip().lower(),
        sender_name=data.get("name"),
        to=data.get("to", []),
        subject=data.get("subject", ""),
        body=data.get("body", ""),
        received_at=datetime.fromisoformat(data["received_at"]),
        attachments=attachments,
    )


def parse_eml(raw: bytes) -> InboundEmail:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    name, sender = parseaddr(str(msg.get("From", "")))
    try:
        received = parsedate_to_datetime(str(msg.get("Date")))
    except (TypeError, ValueError):
        received = datetime.now(timezone.utc)
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = body_part.get_content() if body_part is not None else ""
    attachments = [
        extract_attachment(part.get_filename() or "attachment", part.get_payload(decode=True) or b"", part.get_content_type())
        for part in msg.iter_attachments()
    ]
    message_id = str(msg.get("Message-ID") or "").strip("<> ") or hashlib.sha1(raw).hexdigest()[:16]
    return InboundEmail(
        message_id=message_id,
        thread_id=str(msg.get("In-Reply-To") or "").strip("<> ") or None,
        sender=sender.lower(),
        sender_name=name or None,
        to=[addr for _, addr in getaddresses([str(msg.get("To", ""))])],
        subject=str(msg.get("Subject", "")),
        body=body,
        received_at=received,
        attachments=attachments,
    )


def _stable_id(data: dict[str, Any]) -> str:
    blob = f"{data.get('from')}|{data.get('subject')}|{data.get('received_at')}"
    return "msg-" + hashlib.sha1(blob.encode()).hexdigest()[:12]


def render_for_model(e: InboundEmail, redact=None) -> str:
    """The email as the model sees it, wrapped in tags that mark it as untrusted data."""
    clean = redact or (lambda s: s)
    lines = [
        "<email>",
        f"From: {e.sender_name + ' ' if e.sender_name else ''}<{e.sender}>",
        f"Received: {e.received_at.isoformat()}",
        f"Subject: {clean(e.subject)}",
        "",
        clean(e.body.strip()),
    ]
    for i, a in enumerate(e.attachments, 1):
        lines.append("")
        if a.readable and a.text:
            lines.append(f"[Attachment {i}: {a.filename} ({a.content_type}){' - ' + a.note if a.note else ''}]")
            lines.append(clean(a.text))
        else:
            lines.append(f"[Attachment {i}: {a.filename} - UNREADABLE: {a.note}]")
    lines.append("</email>")
    return "\n".join(lines)
