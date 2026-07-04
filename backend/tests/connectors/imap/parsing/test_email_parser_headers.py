"""
Role: Unit tests for the CA-CONN-05 stored-header data-minimization allowlist — only the
      identity/threading/addressing/flag-source headers are RETAINED; the dense PII + secret
      surface (Received chains, Authentication-Results, Authorization, X-*, Bcc) is dropped at
      storage, while the derived fields still read the FULL header set first. Split out of
      test_email_parser.py for the A2 file-size cap. Pure — no DB, no network.
Used by: pytest (tests/connectors/imap/parsing).
Depends on: app.connectors.imap.parsing.email_parser.
"""

from __future__ import annotations

from app.connectors.imap.parsing.email_parser import parse_email

MAILBOX = "me@oneai.com"


def _eml(headers: str, body: str) -> bytes:
    """Assemble raw RFC822 bytes from a header block + body (CRLF line endings, blank separator)."""
    return (headers.strip() + "\n\n" + body).replace("\n", "\r\n").encode("utf-8")


def test_stored_headers_are_data_minimized_allowlist() -> None:
    # CA-CONN-05: only the identity/threading/addressing/flag-source headers are RETAINED; the
    # dense PII + secret surface (Received chains, Authentication-Results, Authorization, X-*, Bcc)
    # is dropped at storage.
    raw = _eml(
        "From: a@x.com\nTo: me@oneai.com\nCc: c@x.com\nSubject: Hi\nMessage-ID: <m@x.com>\n"
        "Date: Mon, 1 Jun 2026 10:00:00 +0000\n"
        "Received: from mail.internal (10.0.0.5) by mx; Mon, 1 Jun 2026 10:00:00 +0000\n"
        "Authentication-Results: mx; spf=pass\nDKIM-Signature: v=1; a=rsa-sha256; b=abc\n"
        "Authorization: Bearer sk-secret-token-value\nX-Originating-IP: [10.0.0.9]\n"
        "Bcc: hidden@x.com\nList-Unsubscribe: <https://x.com/unsub>",
        "body",
    )

    stored = {key.lower() for key in parse_email(raw, MAILBOX).headers}

    assert {"message-id", "subject", "from", "to", "cc", "date", "list-unsubscribe"} <= stored
    assert stored.isdisjoint(
        {
            "received",
            "authentication-results",
            "dkim-signature",
            "authorization",
            "x-originating-ip",
            "bcc",
        }
    )


def test_received_at_derives_from_received_header_even_though_it_is_dropped() -> None:
    # Proves derive-from-FULL-then-store-minimized: received_at is computed from the Received
    # header (no Date / INTERNALDATE present), yet Received is NOT retained in the stored headers.
    raw = _eml(
        "From: a@x.com\nTo: me@oneai.com\nSubject: H\nMessage-ID: <m@x.com>\n"
        "Received: from mx (1.2.3.4) by host; Tue, 2 Jun 2026 09:00:00 +0000",
        "body",
    )

    parsed = parse_email(raw, MAILBOX)

    assert parsed.received_at is not None  # derived from the (full) Received header
    assert "received" not in {key.lower() for key in parsed.headers}  # but not retained


# — M1 (2026-07-03 audit): stored header keys are canonical-cased, never wire-case —


def test_stored_header_keys_are_canonical_cased() -> None:
    # Senders emit `Message-Id`, `CC`, `Return-path` — wire-case storage split one logical header
    # across JSONB key variants, so `headers->>'Message-ID'` silently missed variant rows.
    raw = _eml(
        "From: a@x.com\nTo: me@oneai.com\nCC: c@x.com\nSubject: Hi\nMessage-Id: <m@x.com>\n"
        "Return-path: <bounce@x.com>\nDate: Mon, 1 Jun 2026 10:00:00 +0000",
        "body",
    )

    stored = parse_email(raw, MAILBOX).headers

    assert "Message-ID" in stored and "Message-Id" not in stored
    assert "Cc" in stored and "CC" not in stored
    assert "Return-Path" in stored and "Return-path" not in stored


def test_case_variant_repeats_of_one_header_merge_into_list() -> None:
    # Two case-variants of the same header within ONE message must merge under the canonical key
    # (first-seen order), not shadow each other.
    raw = _eml(
        "From: a@x.com\nTo: me@oneai.com\nMessage-ID: <m@x.com>\n"
        "Cc: first@x.com\nCC: second@x.com",
        "body",
    )

    stored = parse_email(raw, MAILBOX).headers

    assert stored["Cc"] == ["first@x.com", "second@x.com"]
    assert "CC" not in stored
