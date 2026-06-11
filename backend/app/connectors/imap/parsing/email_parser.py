"""
Role: Deterministic RFC822 → structured decomposition (design §4). Turns raw email bytes into a
      ParsedEmail (headers, addresses, body text, attachment metadata + transient bytes, derived
      flags) with NO database or network access — a pure function the ingest runner maps to rows.
Used by: the IMAP ingest runner (step 3d) and the attachment extractor (consumes ParsedAttachment).
Depends on: stdlib email (BytesParser, policy.default, getaddresses), html2text (html→text body),
            app.connectors.imap.parsing.headers (header/identity/date primitives), .flags, .models.
Key invariants:
  - ROBUST by construction: every header/body/charset decode has an errors='replace' fallback, so a
    malformed-but-parseable email yields a best-effort ParsedEmail. parse_email ALSO catches a
    RecursionError from pathological multipart nesting → a degraded parse_status='failed' stub: the
    email's EXISTENCE + identity are preserved (a queryable flagged row, NOT a silent drop), but its
    CONTENT is lost. For deterministic deep-nesting a re-parse fails identically, so it's correct;
    the residual is a NON-deterministic RecursionError (e.g. a deeper ambient stack in prod) — the
    stub is then content-downgraded until an operator deletes the failed row and re-syncs (exists()
    skips the same bytes on the content key; a re-parse path for 'failed' rows is a follow-up). Any
    OTHER exception PROPAGATES so the runner retries it — a transient parse fault must never be
    frozen as a permanent empty stub.
  - `dedup_key` is a CONTENT IDENTITY (see _dedup_key): a hash of `Message-ID + From/Subject/Date +
    body bytes` with a usable Message-ID, else `sha256(raw_bytes)`. Folder-stable: folder copies
    differ only by prepended trace headers, so all hashed parts match and dedup to ONE row (DQ-B05;
    raw-byte-only keying stored ~40% dups). No over-dedup: two distinct emails sharing a reused
    Message-ID + body but differing in From/Subject/Date get distinct keys — both stored (C02 +
    appliance-sender guard). The body BYTES are hashed, never the derived `body_text` (which shifts
    across html2text versions).
  - `body_text` is text/plain when available, else text/html flattened to text. No HTML is stored.
  - direction/is_reply/is_automated are computed in .flags; header decode lives in .headers; this
    module only orchestrates.
"""

from __future__ import annotations

import logging
from datetime import datetime
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import getaddresses
from hashlib import sha256
from typing import cast

import html2text

from app.connectors.imap.parsing.flags import (
    derive_direction,
    is_automated_origin,
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

logger = logging.getLogger(__name__)


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
        A fully-populated ParsedEmail (parse_status='parsed') on success. A RecursionError from
        pathological multipart nesting is caught → a degraded parse_status='failed' stub (STORED,
        not dropped). Any OTHER exception PROPAGATES — the runner then retries the message next run.
    """
    try:
        return _parse_email_strict(raw_bytes, mailbox_address, internal_date)
    except RecursionError:
        # ONLY RecursionError (a pathological deep multipart overflowing the C stack) is caught: it
        # is (for data-driven nesting) deterministic, so storing a degraded content-keyed stub
        # (flagged 'failed') records the email's existence without wedging the folder. Every OTHER
        # exception is left to PROPAGATE so the runner treats it as a transient failure and RETRIES
        # next run; catching it here would freeze a recoverable email as a permanent empty stub (the
        # content-hash dedup_key would then make exists() skip it forever).
        logger.warning(
            "parse_email: degraded parse on pathological nesting (key=sha256:%s)",
            sha256(raw_bytes).hexdigest(),
            exc_info=True,
        )
        return _degraded_parse(raw_bytes, internal_date)


def _degraded_parse(raw_bytes: bytes, internal_date: datetime | None) -> ParsedEmail:
    """Minimal content-keyed ParsedEmail for an unparseable message (parse_status='failed')."""
    return ParsedEmail(
        dedup_key=f"sha256:{sha256(raw_bytes).hexdigest()}",
        message_id=None,
        in_reply_to=None,
        references=[],
        from_name=None,
        from_address=None,
        subject=None,
        sent_at=None,
        received_at=internal_date,
        body_text="",
        recipients=[],
        attachments=[],
        direction=None,
        is_automated=False,
        is_automated_origin=False,
        is_reply=False,
        has_attachments=False,
        word_count=0,
        language=None,
        headers={},
        parse_status="failed",
    )


def _dedup_key(message: EmailMessage, message_id: str | None, raw_bytes: bytes) -> str:
    """The CONTENT-IDENTITY dedup key — stable across IMAP folders, no over- or under-dedup.

    Hashes `Message-ID + the logical headers (From/Subject/Date) + the body bytes`. All are
    identical across a message's folder copies (which differ ONLY by prepended trace headers like
    Received/X-*), so the copies share one key and dedup to ONE row (DQ-B05). And they DIFFER
    between distinct emails — two messages sharing a (reused) Message-ID and an identical body but
    varying in From/Subject/Date get DISTINCT keys and are BOTH stored, never silently skipped
    (poison resistance + the appliance-sender over-dedup guard). No usable Message-ID, or no
    isolable body, falls back to the injective full raw-byte hash.
    """
    mid_ok = message_id is not None and "\x00" not in message_id and len(message_id) <= MSGID_MAX
    body = _body_bytes(raw_bytes) if mid_ok else b""
    if mid_ok and body:
        # Folder-stable logical identity: the Message-ID + the headers an MTA does NOT rewrite when
        # copying between folders, distinguishing recurring appliance events (Subject/Date vary) so
        # an identical-body reuse of a Message-ID is not silently deduped away.
        logical = "\x00".join(
            [
                message_id,
                *(safe_header(message, name) or "" for name in ("From", "Subject", "Date")),
            ]
        )
        identity = logical.encode("utf-8", "replace") + b"\x00" + body
        return f"sha256:{sha256(identity).hexdigest()}"
    # No usable Message-ID, or no isolable body (headers-only/malformed) → full raw-byte hash, which
    # is injective on distinct bytes (distinct emails stay distinct) but won't dedup that rare case.
    return f"sha256:{sha256(raw_bytes).hexdigest()}"


def _body_bytes(raw_bytes: bytes) -> bytes:
    """Return the message body (bytes after the first header/body separator), or b'' if none.

    Splitting at the first blank line drops the header block — the part that differs across IMAP
    folder copies — leaving the body, which is identical across folders (the folder-stability of the
    dedup key). CRLF (`\\r\\n\\r\\n`) or bare-LF (`\\n\\n`), whichever comes first.
    """
    separators = [(raw_bytes.find(b"\r\n\r\n"), 4), (raw_bytes.find(b"\n\n"), 2)]
    present = [(index, length) for index, length in separators if index != -1]
    if not present:
        return b""
    index, length = min(present)
    return raw_bytes[index + length :]


def _parse_email_strict(
    raw_bytes: bytes, mailbox_address: str, internal_date: datetime | None = None
) -> ParsedEmail:
    """The strict parse (may raise on pathological input); parse_email wraps it to never raise."""
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

    return ParsedEmail(
        dedup_key=_dedup_key(message, message_id, raw_bytes),
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
        is_automated_origin=is_automated_origin(from_address, headers),
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
