"""
Role: Deterministic derived-flag logic for a parsed email — direction (inbound/outbound), reply
      detection, and automated/bulk-sender detection. Split out of email_parser so the parser stays
      a thin orchestrator (code-quality A2: keep email_parser under the 300-line soft cap).
Used by: app.connectors.imap.parsing.email_parser.
Depends on: stdlib only (re).
Key invariants:
  - Pure functions, no I/O. Inputs are already-decoded header values (str) + addresses.
  - `direction` is derived from from_address vs the mailbox account (design §4: NOT from the
    folder — folders are dropped). None when the sender is unknown (can't classify honestly).
  - Address comparison here is a LIGHT lowercase/strip only. The canonical match-key normalizer is
    the entity resolver's job (step 3c); this module must not anticipate it.
"""

from __future__ import annotations

import re

# Re:/Aw:/Antw:/Sv: etc. — common reply prefixes across EN/DE and a few EU locales (DACH focus).
# NOTE: 'Ref:' is deliberately excluded — it is a business reference-number prefix, not a reply.
_REPLY_PREFIX = re.compile(r"^\s*(re|aw|antw|sv|vs)\s*(\[\d+\])?\s*:", re.IGNORECASE)

# Local-parts that are structurally non-human senders (role/automation mailboxes). Conservative on
# purpose — header signals below are more reliable; this only catches the obvious ones.
_AUTOMATED_LOCALPARTS = frozenset(
    {
        "noreply",
        "no-reply",
        "no_reply",
        "donotreply",
        "do-not-reply",
        "do_not_reply",
        "mailer-daemon",
        "mailerdaemon",
        "postmaster",
        "bounce",
        "bounces",
        "bounce-noreply",
    }
)

# Precedence values that mark bulk/automated mail (RFC 2076 convention).
_BULK_PRECEDENCE = frozenset({"bulk", "list", "junk", "auto_reply"})


def normalize_for_compare(address: str | None) -> str | None:
    """Lowercase + strip an address for equality checks (NOT the canonical resolver key)."""
    if not address:
        return None
    cleaned = address.strip().lower()
    return cleaned or None


def derive_direction(from_address: str | None, mailbox_address: str) -> str | None:
    """Classify an email as 'outbound' (the mailbox owner sent it) or 'inbound' (someone else did).

    Returns None when the sender is unknown — we never guess a direction we can't justify. Derived
    from the From address vs the mailbox account, deliberately ignoring which folder it sat in.
    """
    sender = normalize_for_compare(from_address)
    mailbox = normalize_for_compare(mailbox_address)
    if sender is None or mailbox is None:
        return None
    return "outbound" if sender == mailbox else "inbound"


def is_reply_email(in_reply_to: str | None, subject: str | None) -> bool:
    """True if the email is a reply — an In-Reply-To header, or a Re:-style subject prefix."""
    if in_reply_to:
        return True
    return bool(subject and _REPLY_PREFIX.match(subject))


def is_automated_sender(from_address: str | None, headers: dict[str, str | list[str]]) -> bool:
    """True if the email looks machine-generated (role mailbox or bulk/auto headers).

    Layered signals (any one suffices): a known automation local-part; an `Auto-Submitted` header
    other than 'no' (RFC 3834); a bulk `Precedence`; or list-mail headers (`List-Id` /
    `List-Unsubscribe`). Used to keep `noreply@`/`info@`-style senders out of the person graph.
    """
    if _has_automated_localpart(from_address):
        return True

    auto_submitted = _first_header_value(headers, "auto-submitted")
    if auto_submitted and auto_submitted.strip().lower() != "no":
        return True

    precedence = _first_header_value(headers, "precedence")
    if precedence and precedence.strip().lower() in _BULK_PRECEDENCE:
        return True

    return _has_any_header(headers, ("list-id", "list-unsubscribe"))


def _has_automated_localpart(from_address: str | None) -> bool:
    normalized = normalize_for_compare(from_address)
    if not normalized or "@" not in normalized:
        return False
    localpart = normalized.rsplit("@", 1)[0]
    return localpart in _AUTOMATED_LOCALPARTS


def _first_header_value(headers: dict[str, str | list[str]], name: str) -> str | None:
    """Case-insensitive lookup returning the first value (headers may map to a list of values)."""
    for key, value in headers.items():
        if key.lower() == name:
            return value[0] if isinstance(value, list) else value
    return None


def _has_any_header(headers: dict[str, str | list[str]], names: tuple[str, ...]) -> bool:
    present = {key.lower() for key in headers}
    return any(name in present for name in names)
