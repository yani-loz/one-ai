"""
Role: Header / identity / date extraction primitives for the email parser — the lower-level decode
      helpers (msg-id-class headers, the full-header dict, date normalization, NUL/length
      sanitization) split out of email_parser so the orchestrator stays under the size cap (A2).
Used by: app.connectors.imap.parsing.email_parser (its only caller).
Depends on: stdlib email (policy, parsers, utils).
Key invariants:
  - Message-ID/In-Reply-To/References/Content-ID are read from the UNSTRUCTURED (compat32) view via
    _parse_id_headers: policy.default's structured Message-ID parser truncates at inner whitespace
    (which would corrupt the logical identity + dedup_key and could collide two distinct ids).
  - _parse_date returns a TIMEZONE-AWARE datetime (naive / '-0000' headers pinned to UTC) so the
    value always satisfies the DateTime(timezone=True) column contract.
  - _sanitize/_strip_nul guarantee no NUL (U+0000) and no over-length value reaches a stored column
    (Postgres text/jsonb reject NUL; varchar errors rather than truncates).
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesHeaderParser
from email.utils import parsedate_to_datetime

_MSGID_RE = re.compile(r"<[^>]+>")
# Column widths the parser must respect — over-long input is already malformed (RFC 5321 caps a real
# addr-spec at 254); truncating it is the safe choice and only ever hits garbage.
ADDR_MAX = 320
MSGID_MAX = 998
CONTENT_TYPE_MAX = 255  # email_attachment.content_type column width


def parse_id_headers(raw_bytes: bytes) -> Message:
    """Header-only parse under compat32 — yields the RAW msg-id-class header strings (un-truncated).

    policy.default registers a structured Message-ID parser that drops everything after the first
    internal whitespace; compat32 keeps the verbatim value, so the regex extraction below sees the
    full id. Header-only (BytesHeaderParser) stops at the body, so this is cheap.
    """
    return BytesHeaderParser(policy=policy.compat32).parsebytes(raw_bytes)


def build_headers(message: EmailMessage) -> dict[str, str | list[str]]:
    """The full header set as a JSON-able dict; a repeated header collapses to a list of values.

    This is the FULL set — the derived flags (is_automated_*, threading) read it. Only the
    `allowlist_headers` subset is STORED (CA-CONN-05 data-minimization); build the full set first
    so the derivations see every signal, then minimize for retention.
    """
    accumulated: dict[str, list[str]] = defaultdict(list)
    for key, value in message.items():
        accumulated[key].append(safe_str(value))
    return {key: (vals[0] if len(vals) == 1 else vals) for key, vals in accumulated.items()}


# The header names RETAINED in storage (CA-CONN-05 data-minimization allowlist), mapping the
# case-folded lookup key → the ONE canonical spelling stored (RFC-conventional case). Kept:
# identity + threading, addressing, and the automation/flag SOURCE headers (so a stored
# is_automated flag stays explainable). Everything else is DROPPED — notably the dense PII + secret
# surface: Received IP/host chains, Authentication-Results / DKIM-Signature / ARC-*, Authorization,
# X-API-Key and other X-* vendor headers, Delivered-To, and Bcc. The derived columns
# (is_automated_*, sent_at/received_at, recipients) already distil what the pipeline needs.
_RETAINED_HEADERS: dict[str, str] = {
    "message-id": "Message-ID",
    "in-reply-to": "In-Reply-To",
    "references": "References",
    "date": "Date",
    "subject": "Subject",
    "from": "From",
    "to": "To",
    "cc": "Cc",
    "reply-to": "Reply-To",
    "sender": "Sender",
    "return-path": "Return-Path",
    "auto-submitted": "Auto-Submitted",
    "precedence": "Precedence",
    "list-id": "List-Id",
    "list-unsubscribe": "List-Unsubscribe",
    "list-post": "List-Post",
    "list-archive": "List-Archive",
    "content-type": "Content-Type",
    "content-language": "Content-Language",
    "importance": "Importance",
    "priority": "Priority",
    "x-priority": "X-Priority",
    "x-auto-response-suppress": "X-Auto-Response-Suppress",
}


def allowlist_headers(headers: dict[str, str | list[str]]) -> dict[str, str | list[str]]:
    """Return only the data-minimized header allowlist for STORAGE (CA-CONN-05), canonical-cased.

    Drops the dense PII + secret surface (Received chains, Authentication-Results, Authorization,
    X-* vendor headers, Bcc) that GDPR data-minimization would otherwise require us to justify and
    that is also where a leaked credential (Authorization/X-API-Key) would land. The derived flags
    were already computed from the FULL header set, so this only narrows what is RETAINED.

    Keys are stored under ONE canonical spelling (2026-07-03 audit M1): senders emit `Message-Id`,
    `CC`, `Return-path` etc., and storing wire-case split the same header across JSONB key variants
    — `headers->>'Message-ID'` silently missed the rest. Case-variant repeats within one message
    merge into a single list value (first-seen order).
    """
    retained: dict[str, str | list[str]] = {}
    for key, value in headers.items():
        canonical = _RETAINED_HEADERS.get(key.lower())
        if canonical is None:
            continue
        if canonical in retained:
            value = _merge_header_values(retained[canonical], value)
        retained[canonical] = value
    return retained


def _merge_header_values(
    first: str | list[str], second: str | list[str]
) -> list[str]:
    """Merge two value sets of one logical header (case-variant keys in a single message)."""
    merged = first if isinstance(first, list) else [first]
    return merged + (second if isinstance(second, list) else [second])


def received_at_from_headers(message: EmailMessage) -> datetime | None:
    """Approximate the receive time from the topmost Received header, else fall back to Date."""
    top_received = message.get("Received")
    if top_received and ";" in str(top_received):
        received = parse_date(str(top_received).rsplit(";", 1)[-1].strip())
        if received is not None:
            return received
    return parse_date(safe_header(message, "Date"))


def parse_date(value: str | None) -> datetime | None:
    """Parse an RFC2822 date header to a TIMEZONE-AWARE datetime; None on absent/garbage input.

    parsedate_to_datetime returns a NAIVE datetime when the header has no zone or a '-0000' zone
    (RFC 5322 'unknown local time'); we pin those to UTC so the value always satisfies the
    DateTime(timezone=True) column contract and never mixes naive/aware datetimes downstream.
    """
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def split_references(value: str | None) -> list[str]:
    """Extract <message-id> tokens from References, bracket-stripped to MATCH message_id/in_reply_to
    (so the ancestor chain actually joins against message_id instead of never matching)."""
    if not value:
        return []
    return [token for tok in _MSGID_RE.findall(value) if (token := clean_message_id(tok))]


def clean_message_id(value: str | None) -> str | None:
    """Normalize a Message-ID/In-Reply-To/Content-ID to its bracket-stripped core, or None.

    Internal whitespace is removed, so a folded id or a malformed id with embedded spaces collapses
    to one canonical token (and can never silently collide with a different id after truncation).
    """
    if not value:
        return None
    match = _MSGID_RE.search(value)
    token = match.group(0) if match else value
    return re.sub(r"\s+", "", token.strip().strip("<>")) or None


def raw_header(headers: Message, name: str) -> str | None:
    """A raw (compat32) header value as str, or None — used for the msg-id-class headers."""
    value = headers.get(name)
    return None if value is None else safe_str(value)


def safe_header(message: EmailMessage, name: str) -> str | None:
    """Decoded header value as str, or None — never raises on a malformed header."""
    raw = message.get(name)
    return None if raw is None else safe_str(raw)


def safe_str(value: object) -> str:
    """str() a (possibly defect-carrying) header object; NUL-stripped, raw-encoded fallback."""
    try:
        text = str(value)
    except (UnicodeDecodeError, ValueError):
        text = value.encode(errors="replace").decode() if isinstance(value, bytes) else repr(value)
    return strip_nul(text)


def strip_nul(value: str) -> str:
    """Strip NUL (U+0000); Postgres text/jsonb reject it, so it must never reach a column."""
    return value.replace("\x00", "")


def sanitize(value: str | None, limit: int) -> str | None:
    """NUL-strip and cap an optional stored string to its column width (None passes through)."""
    return None if value is None else strip_nul(value)[:limit]
