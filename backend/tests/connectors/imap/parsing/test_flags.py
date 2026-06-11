"""
Role: Unit tests for derived-flag logic — direction, reply detection, automated/bulk-sender
      detection across local-part (token-matched, audit H-4 — compound localparts like
      drive-shares-dm-noreply), non-addr-spec Exchange-NDR From tokens (audit L-7),
      Auto-Submitted, Precedence, and List-* header signals.
Used by: pytest (tests/connectors/imap/parsing). Pure functions, no I/O.
Depends on: app.connectors.imap.parsing.flags.
"""

from __future__ import annotations

import pytest

from app.connectors.imap.parsing.flags import (
    derive_direction,
    is_automated_origin,
    is_automated_sender,
    is_reply_email,
)


@pytest.mark.parametrize(
    ("from_address", "expected"),
    [
        ("me@oneai.com", "outbound"),
        ("ME@OneAI.com", "outbound"),  # case-insensitive
        ("client@acme.com", "inbound"),
        (None, None),
        ("", None),
    ],
)
def test_derive_direction(from_address: str | None, expected: str | None) -> None:
    assert derive_direction(from_address, "me@oneai.com") == expected


def test_derive_direction_none_when_mailbox_blank() -> None:
    assert derive_direction("a@x.com", "") is None


@pytest.mark.parametrize(
    ("in_reply_to", "subject", "expected"),
    [
        ("<prev@x>", None, True),
        (None, "Re: hello", True),
        (None, "RE: hello", True),
        (None, "Aw: hallo", True),  # German reply prefix
        (None, "Antw: hallo", True),
        (None, "Ref: 2024-00123", False),  # reference-number prefix is NOT a reply
        (None, "Fresh subject", False),
        (None, None, False),
    ],
)
def test_is_reply_email(in_reply_to: str | None, subject: str | None, expected: bool) -> None:
    assert is_reply_email(in_reply_to, subject) == expected


@pytest.mark.parametrize(
    "from_address",
    ["noreply@x.com", "no-reply@x.com", "mailer-daemon@x.com", "bounces@x.com", "POSTMASTER@x.com"],
)
def test_is_automated_sender_by_localpart(from_address: str) -> None:
    assert is_automated_sender(from_address, {}) is True


def test_is_automated_sender_human_localpart_is_false() -> None:
    assert is_automated_sender("boyan@acme.com", {}) is False


@pytest.mark.parametrize(
    "from_address",
    [
        # Audit H-4: compound automation localparts that the old exact-set guard let through
        # (the 17 drive-shares messages stored is_automated=false).
        "drive-shares-dm-noreply@google.com",
        "meetings-noreply@google.com",
        "comments-noreply@docs.google.com",
        "account-security-noreply@accountprotection.microsoft.com",
        "invitation-do-not-reply@trello.com",
        "messaging-digest-noreply@linkedin.com",
        "no-reply-cod6h9qg_bo7vatgemoytg@mail.anthropic.com",
        "bounce-noreply@x.com",
    ],
)
def test_is_automated_sender_compound_localpart_returns_true(from_address: str) -> None:
    assert is_automated_sender(from_address, {}) is True
    assert is_automated_origin(from_address, {}) is True  # the person-hood gate must fire too


@pytest.mark.parametrize(
    "from_address",
    ["renotify@acme.com", "abounce@acme.com", "anna-maria.schmidt@acme.de"],
)
def test_is_automated_sender_token_lookalike_returns_false(from_address: str) -> None:
    # H-4 false-positive guard: token boundaries, never substrings — 'renotify' / 'abounce'
    # contain automation words but are not automation tokens; hyphenated humans stay human.
    assert is_automated_sender(from_address, {}) is False
    assert is_automated_origin(from_address, {}) is False


def test_is_automated_sender_non_addr_spec_from_returns_true() -> None:
    # Audit L-7: Exchange NDRs put a bare display token in From (no addr-spec) — the sender
    # identity is machine-shaped and must classify as automated on BOTH predicates.
    assert is_automated_sender("System Administrator", {}) is True
    assert is_automated_origin("System Administrator", {}) is True


def test_is_automated_sender_absent_from_returns_false() -> None:
    # An ABSENT/empty From is not evidence of automation (unsent drafts, B-6) — never guess.
    assert is_automated_sender(None, {}) is False
    assert is_automated_sender("", {}) is False
    assert is_automated_origin(None, {}) is False


def test_is_automated_sender_auto_submitted_header() -> None:
    assert is_automated_sender("a@x.com", {"Auto-Submitted": "auto-generated"}) is True
    assert is_automated_sender("a@x.com", {"Auto-Submitted": "no"}) is False


def test_is_automated_sender_bulk_precedence() -> None:
    assert is_automated_sender("a@x.com", {"Precedence": "bulk"}) is True


def test_is_automated_sender_list_headers() -> None:
    assert is_automated_sender("a@x.com", {"List-Unsubscribe": "<mailto:u@x>"}) is True
    assert is_automated_sender("a@x.com", {"List-Id": "news.x.com"}) is True


def test_is_automated_origin_fires_on_sender_identity_only() -> None:
    # Person-hood gate (DQ-C01): an automation local-part or Auto-Submitted: auto-* IS automated.
    assert is_automated_origin("noreply@x.com", {}) is True
    assert is_automated_origin("a@x.com", {"Auto-Submitted": "auto-generated"}) is True
    assert is_automated_origin("a@x.com", {"Auto-Submitted": "no"}) is False
    assert is_automated_origin("boyan@acme.com", {}) is False


def test_is_automated_origin_ignores_list_routing_so_human_on_list_stays_a_person() -> None:
    # The fix: List-*/Precedence tag a HUMAN's mail traveling through a mailing list — those must
    # NOT suppress the sender's person-hood (is_automated_sender True here; origin does NOT).
    list_headers = {"List-Id": "team.acme.com", "Precedence": "list"}
    assert is_automated_sender("boyan@acme.com", list_headers) is True  # message IS list/bulk
    assert is_automated_origin("boyan@acme.com", list_headers) is False  # sender is human
