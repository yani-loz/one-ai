"""
Role: The AC19 owner-copy redaction — what a NON-OWNER grant-holder may be served from another
      owner's email_message row. A stored email is the OWNER's copy (on Sent copies it carries
      the BCC set); a recipient's own source copy has no BCC line and no owner-only context, so
      a grant to a non-owner resolves to THIS projection, never the raw row.
Used by: the future retrieval layer (Ask) — specified and tested NOW so the contract exists
      before any tool serves email content; also usable by any interim admin/debug surface.
Depends on: app.connectors.imap.models.email (read-only field access; pure function, no I/O).
Key invariants:
  - BCC IS NEVER SERVED to a non-owner: recipient rows of kind 'bcc' are dropped (the stored
    headers already carry no Bcc — the CA-CONN-05 allowlist cut it at parse).
  - Headers are reduced further to the RECIPIENT-VISIBLE subset: a recipient's copy shows
    threading/addressing headers but not the owner's Return-Path or automation-source headers.
  - No owner-only context: nothing connection-/mailbox-identifying beyond the content itself
    (no connection ids, no sync metadata).
  - PURE: no session, no queries — callers pass the rows they already loaded under RLS.
"""

from __future__ import annotations

from typing import Any

from app.connectors.imap.models.email import EmailMessage, EmailRecipient

# Headers a recipient's own source copy would show (threading + addressing + subject/date).
# Deliberately a NARROWER set than parsing/headers._RETAINED_HEADERS (the storage allowlist) —
# NOT derived from it, because the two answer different questions: what may be STORED on the
# owner's copy vs what a NON-OWNER may be SERVED. Excluded on purpose: Return-Path (owner-side
# trace), the automation-flag source headers (Auto-Submitted / List-* / Precedence /
# X-Auto-Response-Suppress), and Content-Type (owner-copy MIME detail). The subset relationship
# (every served header must also be storable) is drift-guarded by
# tests/access/test_email_projection.py::test_served_headers_are_a_subset_of_the_stored_allowlist.
_RECIPIENT_VISIBLE_HEADERS: frozenset[str] = frozenset(
    {
        "Message-ID",
        "In-Reply-To",
        "References",
        "Date",
        "Subject",
        "From",
        "To",
        "Cc",
        "Reply-To",
        "Sender",
    }
)


def project_email_for_non_owner(
    message: EmailMessage, recipients: list[EmailRecipient]
) -> dict[str, Any]:
    """Redact one owner-copy email to the recipient's source-view (AC19).

    Returns a plain dict (never the ORM row): envelope + body + the non-BCC recipient list +
    the recipient-visible header subset. `Any` in the signature: the projection mixes strings,
    datetimes, and nested lists — the shape is this function's documented contract.
    """
    visible_recipients = [
        {"kind": r.kind, "name": r.name, "address": r.address}
        for r in recipients
        if r.kind != "bcc"
    ]
    visible_headers = {
        name: value
        for name, value in (message.headers or {}).items()
        if name in _RECIPIENT_VISIBLE_HEADERS
    }
    return {
        "message_id": message.message_id,
        "from_name": message.from_name,
        "from_address": message.from_address,
        "subject": message.subject,
        "sent_at": message.sent_at,
        "body_text": message.body_text,
        "recipients": visible_recipients,
        "headers": visible_headers,
    }
