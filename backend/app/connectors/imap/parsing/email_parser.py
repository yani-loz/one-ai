"""
Role: Deterministic RFC822 → structured decomposition (design §4). Turns raw email bytes into a
      ParsedEmail (headers, addresses, body text, attachment metadata + transient bytes, derived
      flags) with NO database or network access — a pure function the ingest runner maps to rows.
Used by: the IMAP ingest runner (step 3d) and the attachment extractor (consumes ParsedAttachment).
Depends on: stdlib email (BytesParser, policy.default, getaddresses), html2text (html→text body),
            app.connectors.imap.parsing.headers (header/identity/date primitives), .flags, .models.
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
  - `dedup_key` is a CONTENT IDENTITY over DECODED content (see _dedup_key): with a usable
    Message-ID it hashes the normalized Message-ID + the decoded From/Subject/Date headers + the
    decoded `body_text` + the decoded text/html body candidate's digest + the sorted attachment
    content hashes; otherwise `sha256(raw_bytes)`. The html digest covers the MIME alternative
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
    Sanitized: canonical LF line endings; C0 control chars stripped EXCEPT tab/LF (audit L-6).
  - Stored `from_address` is addr-spec shaped (contains '@') or None: a bare display token in From
    (Exchange NDR 'System Administrator', audit L-7) is NOT stored as an address — the raw token
    stays in `headers` and still feeds the .flags automation classifiers.
  - direction/is_reply/is_automated are computed in .flags; header decode lives in .headers; this
    module only orchestrates.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import getaddresses
from hashlib import sha256
from typing import cast

import html2text
from tnefparse import TNEF

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

# C0 control chars EXCEPT tab (\x09), LF (\x0A), CR (\x0D) — stripped from body_text (audit L-6:
# BEL/VT-class garbage pollutes chunking/embedding; NUL would crash Postgres). CR is exempted here
# only because the LF-normalization pass in _extract_body_text has already removed it.
_BODY_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

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


def _dedup_key(
    message: EmailMessage,
    message_id: str | None,
    body_text: str,
    attachments: list[ParsedAttachment],
    raw_bytes: bytes,
) -> str:
    """The CONTENT-IDENTITY dedup key — folder-independent, no over- or under-dedup.

    With a usable Message-ID, hashes DECODED logical content: the normalized Message-ID + the
    decoded From/Subject headers + the UTC-NORMALIZED send instant (_date_instant — Outlook
    re-renders the raw Date header in a different timezone per folder copy) + the canonical
    To/Cc envelope (_recipient_identity — distinct sends to different audiences must not fold;
    Bcc excluded because only the Sent copy carries it) + the decoded/sanitized `body_text` +
    the decoded text/html body candidate's digest (_html_body_digest — covers the alternative
    `body_text` does NOT select; without it a static plain stub + HTML-only differences silently
    fold) + the SORTED per-attachment identities (_attachment_identity: filename digest + the
    decoded payload's sha256 as computed for email_attachment.content_hash — reused, never
    re-decoded — except the volatile TNEF container, which contributes its STABLE INTERIOR
    digest: embedded-attachment bytes only, verified stable across all 271 multi-copy TNEF
    groups while raw blobs and the RTF body regenerate per copy). Raw body bytes must
    NOT participate: Outlook regenerates `----=_NextPart_...` MIME boundaries and re-wraps
    quoted-printable/base64 in every folder copy's serialization, so a raw-byte hash fragments per
    copy (audit H-1 stored 39.3% duplicate rows); after decoding that variance vanishes and all
    copies share ONE key. Distinct emails REUSING a Message-ID (planted decoy, appliance sender)
    differ in at least one decoded field → distinct keys, so nothing genuine is silently skipped.
    Injective encoding: the NUL-joined, fixed-position fields are individually NUL-free
    (message_id is NUL-checked, safe_header/body_text are NUL-stripped, digests are hex), and the
    final encode uses surrogateescape (raw undecodable header bytes round-trip as their ORIGINAL
    bytes — errors='replace' would collapse every non-ASCII byte to '?', folding two distinct
    8-bit Cyrillic subjects into one key) with a backslashreplace fallback if a surrogate ever
    falls outside the escape range.

    No usable Message-ID → sha256 of the raw bytes: stable across re-fetches of the SAME copy and
    injective on distinct bytes, but folder copies of an id-less message do not fold (rare:
    drafts, malformed mail). The SAME raw-byte fallback applies to a HEADERS-ONLY message (empty
    body, no html alternative, no attachments) even WITH a Message-ID: metadata fields alone
    cannot distinguish repeated appliance notifications that reuse one Message-ID and differ only
    in trace headers — content-keying those would silently skip every event after the first.

    KEY VERSIONING: changing this recipe (or the decode pipeline feeding body_text/content hashes)
    invalidates previously-stored keys — old rows then duplicate against a re-fetch instead of
    deduping (fails open to duplication, never to data loss). Accepted pre-production; the dev
    corpus is wiped and re-ingested after a recipe change.
    """
    if message_id is None or "\x00" in message_id or len(message_id) > MSGID_MAX:
        return f"sha256:{sha256(raw_bytes).hexdigest()}"
    html_digest = _html_body_digest(message)
    if not body_text and not html_digest and not attachments:
        return f"sha256:{sha256(raw_bytes).hexdigest()}"
    identity = "\x00".join(
        [
            message_id,
            *(safe_header(message, name) or "" for name in ("From", "Subject")),
            _date_instant(message),
            _recipient_identity(message),
            body_text,
            html_digest,
            *sorted(_attachment_identity(attachment) for attachment in attachments),
        ]
    )
    try:
        identity_bytes = identity.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError:
        identity_bytes = identity.encode("utf-8", "backslashreplace")
    return f"sha256:{sha256(identity_bytes).hexdigest()}"


def _date_instant(message: EmailMessage) -> str:
    """The Date header as a UTC-normalized INSTANT string (raw header if unparseable).

    Outlook re-renders the Date header in a different timezone per folder copy of the SAME email
    (verified on the corpus: `10:43:53 +0200` in one folder, `11:43:53 +0300` in another — the
    identical instant; 649 duplicate groups survived on exactly this). Keying on the parsed
    instant erases the rendering variance while still splitting genuinely distinct sends, which
    differ in the actual moment, not its formatting. An unparseable Date falls back to the raw
    decoded header so the field still participates injectively.
    """
    raw = safe_header(message, "Date") or ""
    parsed = parse_date(raw)
    return parsed.astimezone(UTC).isoformat() if parsed is not None else raw


def _attachment_identity(attachment: ParsedAttachment) -> str:
    """The attachment's dedup-key contribution: filename digest + content hash (TNEF: interior).

    `application/ms-tnef` (winmail.dat) is a VOLATILE CONTAINER: Outlook regenerates the blob per
    folder copy (Thread-Index and transport properties live inside it), so its raw decoded bytes
    differ per copy of the same email (verified: ALL 253 attachment-divergent duplicate groups on
    the corpus involved TNEF; zero without it). A bare presence marker would fold two DISTINCT
    emails differing only in files embedded INSIDE the TNEF (silent loss — 2026-06-11 cross-vendor
    review), so TNEF contributes its INTERIOR digest instead: the embedded attachments' bytes,
    which are byte-stable across regeneration (verified on ALL 271 multi-copy TNEF groups in the
    corpus) while the volatile transport properties and the RTF body (the instability source,
    whose content already surfaces in the plain-text stub `body_text` hashes) are excluded.

    Non-TNEF attachments contribute the sanitized filename's digest alongside the payload hash:
    two distinct sends differing only in an attachment's NAME (same bytes) must not fold; folder
    copies keep their filenames verbatim, so this adds no under-dedup risk.
    """
    if attachment.content_type == "application/ms-tnef":
        return f"tnef:{_tnef_interior_digest(attachment.payload)}"
    filename_digest = sha256(
        (attachment.filename or "").encode("utf-8", "surrogateescape")
    ).hexdigest()
    return f"{filename_digest}:{attachment.content_hash}"


def _tnef_interior_digest(payload: bytes) -> str:
    """Digest of a TNEF container's STABLE interior: the embedded attachments' bytes only.

    Sorted per-attachment sha256, re-hashed. The TNEF body (RTF) is deliberately EXCLUDED — it is
    the re-serialization instability source (4/40 sampled groups varied on it) and its content is
    already represented in the key via the derived plain-text stub. 'empty' = parsed, nothing
    embedded. 'unparseable' = tnefparse failed: copies of one email then still fold (both
    unparseable → same token) at the cost of re-accepting the narrow marker-collision class for
    corrupt blobs only — documented residual.
    """
    try:
        interior = TNEF(payload)
        parts = sorted(
            sha256(embedded.data).hexdigest()
            for embedded in interior.attachments
            if embedded.data
        )
    except Exception:  # tnefparse raises diverse internals on corrupt blobs — degrade, never fail
        return "unparseable"
    if not parts:
        return "empty"
    return sha256("|".join(parts).encode()).hexdigest()


def _recipient_identity(message: EmailMessage) -> str:
    """Canonical To/Cc envelope for the dedup key: sorted, case-folded `kind:address` pairs.

    Two distinct sends reusing a Message-ID at the same instant with identical content but
    DIFFERENT recipients must not fold (2026-06-11 cross-vendor review). Bcc is deliberately
    EXCLUDED: the mailbox owner's Sent copy carries the Bcc header while every received copy of
    the same logical email does not — including it would split copies that must fold. Reply-To/
    Sender are likewise excluded (re-serialization may add Sender; they don't define the
    audience). Reads the same getaddresses path as _extract_recipients.
    """
    pairs = sorted(
        f"{kind}:{address.strip().lower()}"
        for kind in ("to", "cc")
        for _, address in getaddresses([str(v) for v in message.get_all(kind, [])])
        if address
    )
    return "|".join(pairs)


def _html_body_digest(message: EmailMessage) -> str:
    """Hex sha256 of the DECODED text/html body candidate, or '' when the message has none.

    The selected body alone is blind to the other MIME alternative: when text/plain is selected,
    iter_attachments never yields the unselected text/html part, so two distinct emails sharing a
    static plain stub (appliance/notification senders shipping the real event only in HTML, or a
    planted decoy differing only in HTML) would otherwise collapse onto ONE dedup_key — permanent
    silent mail loss. Hashing the DECODED html (same strict charset chain as body_text, line
    endings normalized to LF) stays folder-independent: Outlook's per-copy boundary regeneration
    and transfer-encoding re-wraps are erased by the decode. Computed even when html IS the
    selected body, so markup-only differences that html2text flattens away still split the key.
    """
    html_part = message.get_body(preferencelist=("html",))
    if html_part is None:
        return ""
    html_text = _decode_text_part(html_part).replace("\r\n", "\n").replace("\r", "\n")
    return sha256(html_text.encode("utf-8", "replace")).hexdigest()


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
        dedup_key=_dedup_key(message, message_id, body_text, attachments, raw_bytes),
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


def _extract_body_text(message: EmailMessage) -> str:
    """Return the body as plain text: text/plain if present, else text/html flattened, else ''."""
    body_part = message.get_body(preferencelist=("plain", "html"))
    if body_part is None:
        return ""
    text = _decode_text_part(body_part)
    if body_part.get_content_type() == "text/html":
        text = _html_to_text(text)
    # Canonical LF line endings (wire CRLF must not leak), THEN strip the remaining C0 controls
    # except tab/LF — NUL would crash Postgres; BEL/VT-class garbage pollutes chunking (audit L-6).
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _BODY_CONTROL_CHARS.sub("", text).strip()


def _decode_text_part(part: EmailMessage) -> str:
    """Decode a text part to str via a STRICT charset-fallback chain (audit M-5).

    Order: declared charset strict → cp1252 strict → windows-1251 strict → declared charset with
    errors='replace' (U+FFFD only when every candidate fails). Senders mislabel constantly — the
    corpus's Outlook gb2312-declared bodies are really cp1252, and Cyrillic mail often hides behind
    a wrong label as windows-1251 — and a strict-first chain recovers those losslessly where the
    old replace-on-declared decode stored replacement chars (41 mojibake rows).
    """
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return _decoded_content(part)
    charset = part.get_content_charset() or "utf-8"
    for candidate in (charset, *_CHARSET_FALLBACKS):
        try:
            return payload.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _decoded_content(part: EmailMessage) -> str:
    """get_content() fallback for the rare part whose decoded payload is not raw bytes."""
    try:
        content = part.get_content()
        return content if isinstance(content, str) else str(content)
    except _DECODE_ERRORS:
        return ""


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
