"""
Role: Unit tests for derived-flag logic — direction, reply detection, automated/bulk-sender
      detection across local-part, Auto-Submitted, Precedence, and List-* header signals.
Used by: pytest (tests/connectors/imap/parsing). Pure functions, no I/O.
Depends on: app.connectors.imap.parsing.flags.
"""

from __future__ import annotations

import pytest

from app.connectors.imap.parsing.flags import (
    derive_direction,
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


def test_is_automated_sender_auto_submitted_header() -> None:
    assert is_automated_sender("a@x.com", {"Auto-Submitted": "auto-generated"}) is True
    assert is_automated_sender("a@x.com", {"Auto-Submitted": "no"}) is False


def test_is_automated_sender_bulk_precedence() -> None:
    assert is_automated_sender("a@x.com", {"Precedence": "bulk"}) is True


def test_is_automated_sender_list_headers() -> None:
    assert is_automated_sender("a@x.com", {"List-Unsubscribe": "<mailto:u@x>"}) is True
    assert is_automated_sender("a@x.com", {"List-Id": "news.x.com"}) is True
