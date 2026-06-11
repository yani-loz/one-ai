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
  - Automation localparts match by TOKEN (split on `-_.+`), not exact membership (audit H-4):
    `drive-shares-dm-noreply@` is automated, `renotify@` / `abounce@` are not (token boundaries,
    never substrings). A non-empty From WITHOUT an addr-spec (Exchange NDR display tokens like
    'System Administrator' — audit L-7) also classifies as automated; an ABSENT From does not
    (absence is not evidence).
  - Address comparison here is a LIGHT lowercase/strip only. The canonical match-key normalizer is
    the entity resolver's job (step 3c); this module must not anticipate it.
"""

from __future__ import annotations

import re

# Re:/Aw:/Antw:/Sv: etc. — common reply prefixes across EN/DE and a few EU locales (DACH focus).
# NOTE: 'Ref:' is deliberately excluded — it is a business reference-number prefix, not a reply.
_REPLY_PREFIX = re.compile(r"^\s*(re|aw|antw|sv|vs)\s*(\[\d+\])?\s*:", re.IGNORECASE)

# Single tokens that mark a structurally non-human sender when they appear as a `-_.+`-delimited
# token of the localpart. Conservative on purpose — header signals below are more reliable; this
# only catches the obvious ones.
_AUTOMATED_TOKENS = frozenset(
    {
        "noreply",
        "donotreply",
        "mailerdaemon",
        "postmaster",
        "bounce",
        "bounces",
    }
)

# Multi-token automation phrases (matched as CONTIGUOUS token runs after splitting): covers
# `no-reply`, `invitation-do-not-reply`, `mailer-daemon`, and their `_`/`.` spellings.
_AUTOMATED_TOKEN_SEQUENCES = frozenset(
    {
        ("no", "reply"),
        ("do", "not", "reply"),
        ("mailer", "daemon"),
    }
)

# Localpart token delimiters — the compound-localpart seam the H-4 phantoms slipped through.
_LOCALPART_TOKEN_SPLIT = re.compile(r"[-_.+]")

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

    Layered signals (any one suffices): a machine-shaped sender identity (automation localpart
    token, or a non-addr-spec From like Exchange's 'System Administrator' NDR token); an
    `Auto-Submitted` header other than 'no' (RFC 3834); a bulk `Precedence`; or list-mail headers
    (`List-Id` / `List-Unsubscribe`). Used to keep `noreply@`/`info@`-style senders out of the
    person graph.
    """
    if _is_machine_sender_identity(from_address):
        return True

    auto_submitted = _first_header_value(headers, "auto-submitted")
    if auto_submitted and auto_submitted.strip().lower() != "no":
        return True

    precedence = _first_header_value(headers, "precedence")
    if precedence and precedence.strip().lower() in _BULK_PRECEDENCE:
        return True

    return _has_any_header(headers, ("list-id", "list-unsubscribe"))


def is_automated_origin(from_address: str | None, headers: dict[str, str | list[str]]) -> bool:
    """True if the SENDER ITSELF is automated — the person-hood gate (DQ-C01), not the bulk flag.

    Uses only sender-IDENTITY signals: a machine-shaped sender identity (automation localpart
    token, or a non-addr-spec Exchange-NDR From token), or an `Auto-Submitted` header other than
    'no' (RFC 3834 — the message was machine-generated). It deliberately does NOT consult the
    list/bulk routing headers (`List-*`, `Precedence: list`) that is_automated_sender does — those
    also tag a REAL human's mail that merely traveled through a mailing list, and dropping that
    human's Person is mail-loss. So a colleague emailing an internal `team@` list still becomes a
    Person, while a `noreply@`/auto-generated sender does not.
    """
    if _is_machine_sender_identity(from_address):
        return True
    auto_submitted = _first_header_value(headers, "auto-submitted")
    return bool(auto_submitted and auto_submitted.strip().lower() != "no")


def _is_machine_sender_identity(from_address: str | None) -> bool:
    """True when the From identity itself is machine-shaped.

    Two signals: (a) the localpart carries an automation token / token phrase (split on `-_.+` —
    audit H-4: compound localparts like `drive-shares-dm-noreply` must match, `renotify` must
    not); (b) the value is non-empty but has NO addr-spec at all — Exchange NDRs put a bare
    display token ('System Administrator') in From (audit L-7). A missing/empty From returns
    False: absence is not evidence of automation.
    """
    normalized = normalize_for_compare(from_address)
    if not normalized:
        return False
    if "@" not in normalized:
        return True
    localpart = normalized.rsplit("@", 1)[0]
    tokens = tuple(token for token in _LOCALPART_TOKEN_SPLIT.split(localpart) if token)
    if any(token in _AUTOMATED_TOKENS for token in tokens):
        return True
    return _has_automated_token_sequence(tokens)


def _has_automated_token_sequence(tokens: tuple[str, ...]) -> bool:
    """True if any contiguous token run matches a multi-token automation phrase."""
    for size in (2, 3):
        for start in range(len(tokens) - size + 1):
            if tokens[start : start + size] in _AUTOMATED_TOKEN_SEQUENCES:
                return True
    return False


def _first_header_value(headers: dict[str, str | list[str]], name: str) -> str | None:
    """Case-insensitive lookup returning the first value (headers may map to a list of values)."""
    for key, value in headers.items():
        if key.lower() == name:
            return value[0] if isinstance(value, list) else value
    return None


def _has_any_header(headers: dict[str, str | list[str]], names: tuple[str, ...]) -> bool:
    present = {key.lower() for key in headers}
    return any(name in present for name in names)
