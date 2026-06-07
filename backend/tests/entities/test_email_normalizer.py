"""
Role: Unit tests for the email match-key normalizer — proves the conservative contract: trim +
      lowercase only, and crucially that Gmail-dot / plus-subaddress canonicalization is NOT applied
      (that would over-merge distinct people), and that .lower() (not .casefold()) is used.
Used by: pytest (tests/entities). Pure, no DB.
Depends on: app.entities.services.email_normalizer.
"""

from __future__ import annotations

import pytest

from app.entities.services.email_normalizer import extract_domain, normalize_email


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Boyan@Acme.COM", "boyan@acme.com"),
        ("  john@x.com  ", "john@x.com"),
        ("<a@x.com>", "a@x.com"),
    ],
)
def test_normalize_email_trims_and_lowercases(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


def test_normalize_email_keeps_plus_subaddress_and_dots() -> None:
    # Provider-specific canonicalization is deliberately OUT — these stay distinct keys.
    assert normalize_email("john.doe+invoices@acme.com") == "john.doe+invoices@acme.com"


def test_normalize_email_uses_lower_not_casefold() -> None:
    # casefold() would map ß→ss and merge two distinct addresses; lower() preserves ß.
    assert normalize_email("STRAßE@x.com") == "straße@x.com"


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("a@acme.com", "acme.com"),
        ("a@sub.acme.co.uk", "sub.acme.co.uk"),
        ("no-at-sign", None),
        ("trailing@", None),
    ],
)
def test_extract_domain(email: str, expected: str | None) -> None:
    assert extract_domain(email) == expected
