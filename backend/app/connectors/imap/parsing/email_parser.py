"""
Role: Deterministic RFC822 → structured decomposition (design §4). Turns raw email bytes into a
      ParsedEmail (headers, addresses, body text, attachment metadata + transient bytes, derived
      flags) with NO database or network access — a pure function the ingest runner maps to rows.
Used by: the IMAP ingest runner (step 3d), the attachment extractor (consumes ParsedAttachment and
         reuses sanitize_body_text + decode_charset_chain + html_to_text for text/html/rtf
         attachments — audits EQ-3/EQ-4), .extractors.pdf and .extractors.tnef (reuse
         sanitize_body_text / html_to_text / decode_charset_chain — the ONE source of the
         stored-text sanitization, charset-decode and HTML-flatten rules).
Depends on: stdlib email (BytesParser, policy.default, getaddresses), html2text (html→text body),
            app.connectors.imap.parsing.headers (header/identity/date primitives), .dedup_key
            (content-identity computation — A2 split), .flags, .models.
Key invariants:
  - ROBUST by construction: every header/body/charset decode ends in an errors='replace' fallback
    (the body decode first walks a STRICT charset chain — declared → cp1252 → windows-1251 — so a
    mislabeled charset is recovered losslessly before any U+FFFD is stored; audit M-5), so a
    malformed-but-parseable email yields a best-effort ParsedEmail. parse_email ALSO catches a
    RecursionError from pathological multipart nesting → a degraded parse_status='failed' stub: the
    email's EXISTENCE + identity are preserved (a queryable flagged row, NOT a silent drop), but its
    CONTENT is lost. For deterministic deep-nesting a re-parse fails identically, so it's correct;
    the residual is a NON-deterministic RecursionError (e.g. a deeper ambient stack in prod) — the
    stub is then content-downgraded until an operator deletes the failed row and re-syncs (exists()
    skips the same bytes on the raw-byte fallback key; a re-parse path for 'failed' rows is a
    follow-up). Any OTHER exception PROPAGATES so the runner retries it — a transient parse fault
    must never be frozen as a permanent empty stub.
  - `dedup_key` is a CONTENT IDENTITY over DECODED content (computed in .dedup_key — the recipe of
    record — v5): with a usable Message-ID it hashes the normalized Message-ID + the decoded
    From/Subject headers + the UTC-NORMALIZED send instant + the canonical To/Cc envelope (Bcc
    excluded — only the Sent copy carries it) + the decoded `body_text` + the decoded text/html
    body candidate's digest + the sorted per-attachment identities (filename digest + content
    hash; TNEF contributes its stable INTERIOR digest — flattened RTF body + embedded bytes);
    otherwise `sha256(raw_bytes)` (also for headers-only messages). The html digest covers the
    MIME alternative
    `body_text` does NOT see when text/plain is selected — without it two distinct emails sharing
    a static plain stub but differing only in HTML would silently fold (appliance senders /
    planted decoys; fixup of the 2026-06-10 review). FOLDER-INDEPENDENT by decoding: Outlook
    re-serializes every folder copy with regenerated `----=_NextPart_...` MIME boundaries and
    re-wrapped transfer encodings INSIDE the raw body, so any raw-byte/raw-body hash fragments per
    copy (audit H-1: 39.3% duplicate rows stored) — decoding erases that serialization variance,
    so all copies of one logical email share ONE key. No over-dedup: distinct emails reusing a
    Message-ID differ in some decoded field → distinct keys. KEY VERSIONING: changing this recipe
    (or the decode pipeline feeding it) invalidates previously-stored keys — re-fetches duplicate
    instead of deduping (fails open to duplication, never to loss). Accepted pre-production; the
    dev corpus is wiped + re-ingested after a recipe change.
  - `body_text` is text/plain when available, else text/html flattened to text. No HTML is stored.
    Sanitized (sanitize_body_text — guarantee: storable as UTF-8): canonical LF line endings; C0
    control chars stripped EXCEPT tab/LF (audit L-6); lone surrogates U+D800–U+DFFF stripped
    (broken PDF ToUnicode CMaps would otherwise crash the asyncpg flush — poison message).
  - Stored `from_address` is addr-spec shaped (contains '@') or None: a bare display token in From
    (Exchange NDR 'System Administrator', audit L-7) is NOT stored as an address — the raw token
    stays in `headers` and still feeds the .flags automation classifiers.
  - direction/is_reply/is_automated are computed in .flags; header decode lives in .headers; this
    module only orchestrates.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import getaddresses
from hashlib import sha256
from typing import cast

import html2text

from app.connectors.imap.parsing.dedup_key import compute_dedup_key
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

# Strict-decode fallbacks for mislabeled charsets (audit M-5): the corpus's Outlook bodies declare
# gb2312 but carry cp1252 bytes; Cyrillic mail often hides behind a wrong label as windows-1251.
_CHARSET_FALLBACKS: tuple[str, ...] = ("cp1252", "windows-1251")

# Code points that must never reach a stored text column:
#   - C0 control chars EXCEPT tab (\x09), LF (\x0A), CR (\x0D) — audit L-6: BEL/VT-class garbage
#     pollutes chunking/embedding; NUL would crash Postgres. CR is exempted here only because the
#     LF-normalization pass in sanitize_body_text has already removed it.
#   - LONE SURROGATES (U+D800–U+DFFF) — pdfminer/pypdf emit them from broken PDF ToUnicode CMaps;
#     they survive in a Python str but asyncpg raises UnicodeEncodeError at flush, turning the
#     whole email into a poison message (2026-06-11 review).
_UNSTORABLE_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]")

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
        # raw-byte dedup_key would then make exists() skip it forever).
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
        dedup_key=compute_dedup_key(
            message, message_id, body_text, attachments, raw_bytes, _decode_text_part
        ),
        message_id=sanitize(message_id, MSGID_MAX),
        in_reply_to=sanitize(in_reply_to, MSGID_MAX),
        references=references,
        from_name=sanitize(from_name, ADDR_MAX),
        # Only an addr-spec-shaped From is STORED as an address (audit L-7); the .flags calls below
        # still receive the raw token — a bare 'System Administrator' classifies as automated.
        from_address=_addr_spec_or_none(sanitize(from_address, ADDR_MAX)),
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


def _addr_spec_or_none(address: str | None) -> str | None:
    """Return the address only when addr-spec shaped (contains '@'), else None (audit L-7).

    Exchange NDRs put a bare display token ('System Administrator') in From; getaddresses surfaces
    it in the ADDRESS slot, and storing it as from_address corrupts every address-shaped consumer.
    The token survives in the stored `headers` JSONB.
    """
    return address if address and "@" in address else None


def sanitize_body_text(text: str) -> str:
    """Apply the canonical stored-text sanitization to a decoded text block.

    The guarantee is: the result is STORABLE AS UTF-8, period. Canonical LF line endings (wire
    CRLF must not leak), THEN strip the remaining C0 controls except tab/LF (audit L-6: NUL would
    crash Postgres; BEL/VT-class garbage pollutes chunking) AND lone surrogates U+D800–U+DFFF
    (pdfminer/pypdf emit them from broken ToUnicode CMaps; asyncpg raises UnicodeEncodeError at
    flush — a poison message), then trim surrounding whitespace. The SINGLE source of these rules:
    email bodies, text attachments (attachment_extractor) AND extracted PDF text (extractors.pdf)
    all pass through here, never a re-implementation.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _UNSTORABLE_CHARS.sub("", text).strip()


def _extract_body_text(message: EmailMessage) -> str:
    """Return the body as plain text: text/plain if present, else text/html flattened, else ''."""
    body_part = message.get_body(preferencelist=("plain", "html"))
    if body_part is None:
        return ""
    text = _decode_text_part(body_part)
    if body_part.get_content_type() == "text/html":
        text = html_to_text(text)
    return sanitize_body_text(text)


def decode_charset_chain(payload: bytes, declared_charset: str | None = None) -> str:
    """Decode bytes to str via the STRICT charset-fallback chain (audits M-5 + EQ-3).

    Order: declared charset strict (utf-8 when none is declared) → cp1252 strict →
    windows-1251 strict → declared charset with errors='replace' (U+FFFD only when every strict
    candidate fails; utf-8-replace if even the declared charset is unknown). Senders mislabel
    constantly — the corpus's Outlook gb2312-declared bodies are really cp1252, and Cyrillic mail
    often hides behind a wrong label as windows-1251 — and a strict-first chain recovers those
    losslessly where a replace-first decode stores replacement chars. The SINGLE chain shared by
    email bodies, text-shaped attachments (attachment_extractor — EQ-3 measured 9 rows / 564K
    mojibake chars under the old utf-8-only decode) and TNEF interiors (extractors.tnef).
    """
    charset = declared_charset or "utf-8"
    for candidate in (charset, *_CHARSET_FALLBACKS):
        try:
            return payload.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _decode_text_part(part: EmailMessage) -> str:
    """Decode a text part to str via decode_charset_chain (the M-5 strict fallback chain)."""
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return _decoded_content(part)
    return decode_charset_chain(payload, part.get_content_charset())


def _decoded_content(part: EmailMessage) -> str:
    """get_content() fallback for the rare part whose decoded payload is not raw bytes."""
    try:
        content = part.get_content()
        return content if isinstance(content, str) else str(content)
    except _DECODE_ERRORS:
        return ""


def html_to_text(html: str) -> str:
    """Flatten HTML to readable text (no wrapping, drop images, keep link/anchor text).

    The SINGLE HTML flattener: email bodies, text/html attachments (attachment_extractor —
    audit EQ-4 found 44 rows storing raw markup source as 'extracted') and TNEF html bodies
    (extractors.tnef) all flatten through here, never a re-implementation.
    """
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
                # `or None` coalesces '' → None: ONE absent-filename encoding (audit L-8).
                filename=sanitize(part.get_filename(), MSGID_MAX) or None,
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
    """Flatten To/Cc/Bcc/Reply-To/Sender headers into per-address recipient rows.

    Deduplicated WITHIN the message per (kind, case-folded address) — clients repeat one mailbox in
    a header (audit M-6: 199 redundant edges stored); the FIRST occurrence wins, keeping its as-seen
    address spelling and display name. The same address under DIFFERENT kinds (to + cc) is kept:
    those are distinct roles, not duplicates.
    """
    recipients: list[ParsedRecipient] = []
    seen_edges: set[tuple[str, str]] = set()
    for header_name, kind in _RECIPIENT_HEADERS:
        raw_values = message.get_all(header_name, [])
        for name, address in getaddresses([str(v) for v in raw_values]):
            if not address:
                continue
            stored_address = sanitize(address, ADDR_MAX) or ""
            edge = (kind, stored_address.lower())
            if edge in seen_edges:
                continue
            seen_edges.add(edge)
            recipients.append(
                ParsedRecipient(
                    kind=kind,
                    address=stored_address,
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
