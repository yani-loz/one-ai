"""
Role: The CONTENT-IDENTITY dedup key (recipe of record — v5) and its component digests. Computes
      the folder-independent identity an email is deduplicated on: Outlook re-serializes every
      folder copy (regenerated MIME boundaries, re-wrapped transfer encodings, re-rendered Date
      timezones, regenerated TNEF containers), so the key hashes DECODED logical content, never
      raw serialization.
Used by: .email_parser (parse_email computes each ParsedEmail's dedup_key through this module).
Depends on: stdlib (hashlib, email.utils), tnefparse (TNEF interior digests),
            app.connectors.extraction.tnef_rtf (MAX_COMPRESSED_RTF_BYTES + RtfBodyOverBoundError +
            flatten_tnef_rtf_body — the shared striprtf-flatten primitive + LZFu-bomb bound, the
            arrow now points OUT to the connector-agnostic extraction package),
            .headers (safe_header / parse_date / MSGID_MAX), .models (ParsedAttachment).
            The text/html decoder is INJECTED by the caller (email_parser._decode_text_part) —
            the decode pipeline stays single-sourced there without an import cycle.
Key invariants:
  - With a usable Message-ID the key hashes: normalized Message-ID + decoded From/Subject + the
    UTC-NORMALIZED send instant + the canonical To/Cc envelope (Bcc excluded — only the Sent copy
    carries it) + decoded body_text + the decoded text/html alternative's digest + sorted
    per-attachment identities (filename digest + content hash; TNEF contributes its stable
    INTERIOR digest). No usable Message-ID, or a HEADERS-ONLY message → sha256(raw_bytes).
  - KEY-CONSISTENCY (v5, mirrored in extractors/tnef.py): the key and the TNEF extractor read
    the SAME TNEF signals — the striprtf-FLATTENED RTF body + the embedded attachments' bytes —
    so content the extractor can materialize is always key-distinguished. The flattened body is
    byte-stable across ALL 271 multi-copy TNEF groups in the corpus (the raw body's 4/40
    instability was pure RTF re-serialization markup, which flattening erases — measured
    2026-06-12); the same MAX_COMPRESSED_RTF_BYTES bound gates both readers.
  - NEVER over-dedup (silent mail loss is the worst failure): two genuinely distinct emails must
    differ in at least one keyed decoded field. NEVER under-dedup on serialization variance:
    every per-copy regeneration source identified on the corpus is normalized away.
  - KEY VERSIONING: changing this recipe (or the decode pipeline feeding it) invalidates
    previously-stored keys — re-fetches then duplicate instead of deduping (fails open to
    duplication, never to loss). Accepted pre-production; the dev corpus is wiped + re-ingested
    after a recipe change. v5 (the flattened-body component) is such a change: the corpus
    re-ingest follows this revision (the lead runs it).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC
from email.message import EmailMessage
from email.utils import getaddresses
from hashlib import sha256

from tnefparse import TNEF

from app.connectors.extraction.tnef_rtf import (
    MAX_COMPRESSED_RTF_BYTES,
    RtfBodyOverBoundError,
    flatten_tnef_rtf_body,
)
from app.connectors.imap.parsing.headers import MSGID_MAX, parse_date, safe_header
from app.connectors.imap.parsing.models import ParsedAttachment

# tnefparse interpolates RAW PAYLOAD BYTES into its log records (live-reproduced 2026-06-12 —
# see extraction/tnef.py, which documents the repro and carries the same mute). This module
# parses the same containers via TNEF(payload), and a process importing it WITHOUT the extractor
# module would have the leak unmuted — so the idempotent setLevel is duplicated here (same
# process-wide CRITICAL posture as pypdf/pdfminer in extraction/pdf.py; security.md).
logging.getLogger("tnefparse").setLevel(logging.CRITICAL)

# The LZFu decompression-bomb bound (MAX_COMPRESSED_RTF_BYTES) and the flatten primitive
# (flatten_tnef_rtf_body) are SINGLE-SOURCED in app.connectors.extraction.tnef_rtf and re-bound
# here. The bound is re-bound into THIS module's namespace and passed explicitly below so the
# dedup-key over-bound branch is still gated by a monkeypatch of dedup_key.MAX_COMPRESSED_RTF_BYTES;
# the key and the extractor must apply the SAME bound, or the two TNEF readers' notions of "the
# body" would drift apart (the KEY-CONSISTENCY invariant).


def compute_dedup_key(
    message: EmailMessage,
    message_id: str | None,
    body_text: str,
    attachments: list[ParsedAttachment],
    raw_bytes: bytes,
    decode_text_part: Callable[[EmailMessage], str],
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
    digest: the striprtf-FLATTENED RTF body + embedded-attachment bytes, both verified stable
    across all 271 multi-copy TNEF groups while raw blobs regenerate per copy). Raw body bytes must
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

    `decode_text_part` is the caller's body-part decoder (the strict charset chain) — injected so
    the html digest decodes EXACTLY like body_text without an email_parser import cycle.
    """
    if message_id is None or "\x00" in message_id or len(message_id) > MSGID_MAX:
        return f"sha256:{sha256(raw_bytes).hexdigest()}"
    html_digest = _html_body_digest(message, decode_text_part)
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
    emails differing only in content INSIDE the TNEF (silent loss — 2026-06-11 cross-vendor
    review + the 2026-06-12 body finding), so TNEF contributes its INTERIOR digest instead: the
    striprtf-FLATTENED RTF body + the embedded attachments' bytes, both byte-stable across
    regeneration (verified on ALL 271 multi-copy TNEF groups in the corpus — the raw RTF body's
    instability was pure re-serialization markup, erased by flattening) while the volatile
    transport properties are excluded.

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
    """Digest of a TNEF container's STABLE interior: flattened RTF body + embedded bytes (v5).

    KEY-CONSISTENCY invariant (mirrored in extractors/tnef.py): the key and the extractor read
    the SAME TNEF signals — the striprtf-FLATTENED body and the embedded attachments' bytes —
    so content the extractor can materialize is always key-distinguished. The body participates
    FLATTENED, never raw: the raw RTF stream is the re-serialization instability source (4/40
    sampled groups varied on it — pure markup regeneration), while the flattened text is
    byte-stable across ALL 271 multi-copy TNEF groups in the corpus (measured 2026-06-12).
    Composition: the flattened-body component (_tnef_flattened_body_component) joined with the
    sorted per-embedded-attachment sha256s, re-hashed. 'empty' = parsed, no RTF body and nothing
    embedded. 'unparseable' = tnefparse/decompress/flatten failed: copies of one email then
    still fold (both unparseable → same token) at the cost of re-accepting the narrow
    marker-collision class for corrupt blobs only — documented residual.
    """
    try:
        interior = TNEF(payload)
        body_component = _tnef_flattened_body_component(interior)
        parts = sorted(
            sha256(embedded.data).hexdigest() for embedded in interior.attachments if embedded.data
        )
    except Exception:  # tnefparse raises diverse internals on corrupt blobs — degrade, never fail
        return "unparseable"
    if not parts and body_component == "no-rtf":
        return "empty"
    return sha256("|".join([body_component, *parts]).encode()).hexdigest()


def _tnef_flattened_body_component(interior: TNEF) -> str:
    """The flattened-RTF-body component of the interior digest (may raise — caller degrades).

    Flattens via the shared app.connectors.extraction.tnef_rtf primitive, passing THIS module's
    MAX_COMPRESSED_RTF_BYTES so a monkeypatch of dedup_key's bound still gates the over-bound
    branch. Absent RTF body (helper returns None) → the fixed token 'no-rtf'; over-bound
    (RtfBodyOverBoundError — never decompressed; the extractor skips the same body for
    KEY-CONSISTENCY) → 'rtf-over-bound'; else sha256 of the flattened+stripped body (the helper
    reads the lazy decompressed body latin-1 and striprtf-flattens with errors='replace' — the
    same read as extraction/tnef.py). Decompress/strip failures PROPAGATE to the digest's except.
    """
    try:
        flattened = flatten_tnef_rtf_body(interior, MAX_COMPRESSED_RTF_BYTES)
    except RtfBodyOverBoundError:
        return "rtf-over-bound"
    if flattened is None:
        return "no-rtf"
    return sha256(flattened.encode("utf-8", "replace")).hexdigest()


def _recipient_identity(message: EmailMessage) -> str:
    """Canonical To/Cc envelope for the dedup key: sorted, case-folded `kind:address` pairs.

    Two distinct sends reusing a Message-ID at the same instant with identical content but
    DIFFERENT recipients must not fold (2026-06-11 cross-vendor review). Bcc is deliberately
    EXCLUDED: the mailbox owner's Sent copy carries the Bcc header while every received copy of
    the same logical email does not — including it would split copies that must fold. Reply-To/
    Sender are likewise excluded (re-serialization may add Sender; they don't define the
    audience). Reads the same getaddresses path as email_parser's recipient extraction.
    """
    pairs = sorted(
        f"{kind}:{address.strip().lower()}"
        for kind in ("to", "cc")
        for _, address in getaddresses([str(v) for v in message.get_all(kind, [])])
        if address
    )
    return "|".join(pairs)


def _html_body_digest(
    message: EmailMessage, decode_text_part: Callable[[EmailMessage], str]
) -> str:
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
    # Hash the DECODED html, line endings normalized only — deliberately NOT whitespace-collapsed
    # or structure-flattened. The html digest's job is anti-collision: when two emails share an
    # identical plain stub (appliance senders, or a TC-IM-C02 planted decoy) the html is the SOLE
    # differentiator, so ANY normalization erasing a real html difference (incl. whitespace inside
    # <pre>/fixed-width tables) would FALSELY collapse distinct mail = mail loss. Never-lose-mail
    # outranks the ~0.2% residual cross-route dup the folder blocklist already largely removed.
    # (A 2026-06-14 whitespace-collapse "optimization" was reverted for exactly this reason.)
    html_text = decode_text_part(html_part).replace("\r\n", "\n").replace("\r", "\n")
    return sha256(html_text.encode("utf-8", "replace")).hexdigest()
