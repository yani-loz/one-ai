"""
Role: Deterministic RFC822 → structured decomposition (design §4). Turns raw email bytes into a
      ParsedEmail (headers, addresses, body text, attachment metadata + transient bytes, derived
      flags) with NO database or network access — a pure function the ingest runner maps to rows.
Used by: the IMAP ingest runner (step 3d) and the attachment extractor (consumes ParsedAttachment).
Depends on: stdlib email (BytesParser, policy.default, getaddresses), html2text (html→text body),
            app.connectors.imap.parsing.headers (header/identity/date primitives), .flags, .models.
Key invariants:
  - NEVER raises on malformed input that the stdlib can parse: every header/body/charset decode has
    an errors='replace' fallback, so a weird email yields a best-effort ParsedEmail, not a crash.
    (A truly unparseable blob may still raise; the runner wraps each call → parse_status='failed'.)
  - `dedup_key` = the Message-ID when present, else `sha256:<hex of the RAW bytes>` — hashed from
    the immutable wire bytes, NEVER from derived body_text (which changes across html2text versions
    and would silently break the idempotent upsert). A NUL/over-length id falls back to the hash.
  - `body_text` is text/plain when available, else text/html flattened to text. No HTML is stored.
  - direction/is_reply/is_automated are computed in .flags; header decode lives in .headers; this
    module only orchestrates.
"""

from __future__ import annotations

from datetime import datetime
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import getaddresses
from hashlib import sha256
from typing import cast

import html2text

from app.connectors.imap.parsing.flags import (
    derive_direction,
    is_automated_sender,
    is_reply_email,
)
from app.connectors.imap.parsing.headers import (
    ADDR_MAX,
    CONTENT_TYPE_MAX,
    MSGID_MAX,
    build_headers,
    clean_message_id,
    parse_date,
    parse_id_headers,
    raw_header,
    received_at_from_headers,
    safe_header,
    sanitize,
    split_references,
    strip_nul,
)
from app.connectors.imap.parsing.models import ParsedAttachment, ParsedEmail, ParsedRecipient

# Header name → recipient kind (email_recipient.kind CHECK). 'sender' comes from the Sender header.
_RECIPIENT_HEADERS: tuple[tuple[str, str], ...] = (
    ("to", "to"),
    ("cc", "cc"),
    ("bcc", "bcc"),
    ("reply-to", "reply_to"),
    ("sender", "sender"),
)
_DECODE_ERRORS = (LookupError, UnicodeDecodeError, ValueError)


def parse_email(
    raw_bytes: bytes, mailbox_address: str, internal_date: datetime | None = None
) -> ParsedEmail:
    """Parse raw RFC822 bytes into a ParsedEmail.

    Args:
        raw_bytes: the full RFC822 message as fetched (BODY.PEEK[]) or read from a .eml file.
        mailbox_address: the synced account's address — used to derive inbound/outbound direction.
        internal_date: the IMAP INTERNALDATE if known (prod fetch path). Authoritative for
            received_at; absent for the disk path, where we fall back to the Received/Date headers.

    Returns:
        A fully-populated ParsedEmail (parse_status='parsed'). Best-effort on malformed input.
    """
    message = _parse_message(raw_bytes)
    id_headers = parse_id_headers(raw_bytes)

    # Message-ID/In-Reply-To/References come from the UNSTRUCTURED (compat32) view: policy.default's
    # structured Message-ID parser truncates at internal whitespace, which would corrupt the logical
    # identity + dedup_key and break threading (a malformed id could collide with a legitimate one).
    message_id = clean_message_id(raw_header(id_headers, "Message-ID"))
    in_reply_to = clean_message_id(raw_header(id_headers, "In-Reply-To"))
    references = split_references(raw_header(id_headers, "References"))
    headers = build_headers(message)
    from_name, from_address = _first_address(message, "from")
    subject = safe_header(message, "Subject")

    body_text = _extract_body_text(message)
    attachments = _extract_attachments(message)

    # A NUL-bearing or over-length Message-ID can't be a safe identity key (storage rejects NUL; a
    # truncated id could collide), so fall back to the content hash; the stored message_id is
    # sanitized separately for its column.
    mid_ok = message_id is not None and "\x00" not in message_id and len(message_id) <= MSGID_MAX
    dedup_key = message_id if mid_ok else f"sha256:{sha256(raw_bytes).hexdigest()}"

    return ParsedEmail(
        dedup_key=dedup_key,
        message_id=sanitize(message_id, MSGID_MAX),
        in_reply_to=sanitize(in_reply_to, MSGID_MAX),
        references=references,
        from_name=sanitize(from_name, ADDR_MAX),
        from_address=sanitize(from_address, ADDR_MAX),
        subject=subject,
        sent_at=parse_date(safe_header(message, "Date")),
        received_at=internal_date or received_at_from_headers(message),
        body_text=body_text,
        recipients=_extract_recipients(message),
        attachments=attachments,
        direction=derive_direction(from_address, mailbox_address),
        is_automated=is_automated_sender(from_address, headers),
        is_reply=is_reply_email(in_reply_to, subject),
        has_attachments=bool(attachments),
        word_count=len(body_text.split()),
        language=None,
        headers=headers,
    )


def _parse_message(raw_bytes: bytes) -> EmailMessage:
    """Parse bytes into an EmailMessage (policy.default → get_body / iter_attachments available)."""
    # policy.default always yields EmailMessage; cast narrows the type without an assert that
    # `python -O` would strip (the guarantee is the policy's, not a runtime check we rely on).
    return cast(EmailMessage, message_from_bytes(raw_bytes, policy=policy.default))


def _extract_body_text(message: EmailMessage) -> str:
    """Return the body as plain text: text/plain if present, else text/html flattened, else ''."""
    body_part = message.get_body(preferencelist=("plain", "html"))
    if body_part is None:
        return ""
    text = _decode_text_part(body_part)
    if body_part.get_content_type() == "text/html":
        text = _html_to_text(text)
    # Canonical LF line endings (wire CRLF must not leak) + NUL stripped (Postgres text rejects it).
    return strip_nul(text.replace("\r\n", "\n").replace("\r", "\n")).strip()


def _decode_text_part(part: EmailMessage) -> str:
    """Decode a text part to str; falls back to a replace-errors decode on a bad/unknown charset."""
    try:
        content = part.get_content()
        return content if isinstance(content, str) else str(content)
    except _DECODE_ERRORS:
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            return ""
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except LookupError:
            return payload.decode("utf-8", errors="replace")


def _html_to_text(html: str) -> str:
    """Flatten HTML to readable text (no wrapping, drop images, keep link/anchor text)."""
    converter = html2text.HTML2Text()
    converter.body_width = 0  # never hard-wrap — wrapping corrupts downstream chunking
    converter.ignore_images = True
    converter.unicode_snob = True
    return converter.handle(html)


def _extract_attachments(message: EmailMessage) -> list[ParsedAttachment]:
    """Collect every non-body part as a ParsedAttachment (metadata + transient raw bytes)."""
    attachments: list[ParsedAttachment] = []
    for part in message.iter_attachments():
        payload = _attachment_bytes(part)
        content_id = sanitize(clean_message_id(safe_header(part, "Content-ID")), MSGID_MAX)
        attachments.append(
            ParsedAttachment(
                filename=sanitize(part.get_filename(), MSGID_MAX),
                content_type=sanitize(part.get_content_type(), CONTENT_TYPE_MAX),
                size_bytes=len(payload),
                content_hash=sha256(payload).hexdigest(),
                is_inline=part.get_content_disposition() == "inline",
                content_id=content_id,
                payload=payload,
            )
        )
    return attachments


def _attachment_bytes(part: EmailMessage) -> bytes:
    """Best-effort raw bytes for an attachment part (decoded payload, else the serialized part)."""
    payload = part.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload
    try:
        return part.as_bytes()
    except (KeyError, ValueError, TypeError):
        return b""


def _extract_recipients(message: EmailMessage) -> list[ParsedRecipient]:
    """Flatten To/Cc/Bcc/Reply-To/Sender headers into per-address recipient rows."""
    recipients: list[ParsedRecipient] = []
    for header_name, kind in _RECIPIENT_HEADERS:
        raw_values = message.get_all(header_name, [])
        for name, address in getaddresses([str(v) for v in raw_values]):
            if address:
                recipients.append(
                    ParsedRecipient(
                        kind=kind,
                        address=sanitize(address, ADDR_MAX) or "",
                        name=sanitize(name, ADDR_MAX) or None,
                    )
                )
    return recipients


def _first_address(message: EmailMessage, header_name: str) -> tuple[str | None, str | None]:
    """Return the (display_name, address) of the first address in a header, or (None, None)."""
    raw_values = message.get_all(header_name, [])
    for name, address in getaddresses([str(v) for v in raw_values]):
        if address:
            return (name or None, address)
    return (None, None)
