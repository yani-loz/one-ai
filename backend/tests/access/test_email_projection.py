"""
Role: AC19 owner-copy redaction tests — the non-owner projection never exposes BCC recipients
      or owner-only headers, and matches what a recipient's own source copy would show.
Used by: pytest (tests/access). Pure — no DB (the projection is a pure function over rows).
Depends on: app.access.services.email_projection.
"""

from __future__ import annotations

from uuid import uuid4

from app.access.services.email_projection import project_email_for_non_owner
from app.connectors.imap.models.email import EmailMessage, EmailRecipient


def _message(headers: dict) -> EmailMessage:
    return EmailMessage(
        org_id=uuid4(),
        connection_id=uuid4(),
        dedup_key="p",
        message_id="<p@x>",
        from_name="Sender",
        from_address="sender@globex.test",
        subject="quarterly numbers",
        body_text="the body",
        headers=headers,
    )


def _recipient(kind: str, address: str) -> EmailRecipient:
    return EmailRecipient(org_id=uuid4(), email_id=uuid4(), kind=kind, address=address, name=None)


def test_non_owner_never_sees_bcc_recipients() -> None:
    recipients = [
        _recipient("to", "alice@acme.test"),
        _recipient("cc", "bob@acme.test"),
        _recipient("bcc", "hidden@acme.test"),
    ]

    projection = project_email_for_non_owner(_message({}), recipients)

    served = {(r["kind"], r["address"]) for r in projection["recipients"]}
    assert ("bcc", "hidden@acme.test") not in served
    assert served == {("to", "alice@acme.test"), ("cc", "bob@acme.test")}
    assert "hidden@acme.test" not in str(projection)  # nowhere in ANY field (AC19)


def test_served_headers_are_a_subset_of_the_stored_allowlist() -> None:
    # Drift guard (2026-07-04 review L13): the projection can only serve what storage retains —
    # if the CA-CONN-05 allowlist ever drops a header the projection still lists, this fails
    # instead of silently serving a permanently-absent key.
    from app.access.services.email_projection import _RECIPIENT_VISIBLE_HEADERS
    from app.connectors.imap.parsing.headers import _RETAINED_HEADERS

    stored_canonical = set(_RETAINED_HEADERS.values())
    assert _RECIPIENT_VISIBLE_HEADERS <= stored_canonical, (
        f"projection serves headers storage never retains: "
        f"{sorted(_RECIPIENT_VISIBLE_HEADERS - stored_canonical)}"
    )


def test_non_owner_projection_matches_recipient_source_view() -> None:
    # Owner-copy headers beyond a recipient's view (Return-Path, automation-source headers,
    # Content-Type) are stripped; the recipient-visible threading/addressing subset survives.
    headers = {
        "Message-ID": "<p@x>",
        "Subject": "quarterly numbers",
        "From": "sender@globex.test",
        "To": "alice@acme.test",
        "Return-Path": "<bounce@globex.test>",
        "Auto-Submitted": "no",
        "Content-Type": "text/plain",
    }

    projection = project_email_for_non_owner(_message(headers), [])

    assert set(projection["headers"]) == {"Message-ID", "Subject", "From", "To"}
    assert projection["body_text"] == "the body"
    assert projection["subject"] == "quarterly numbers"
