"""
Role: get_email — fetch ONE message in full (body, recipients, attachment list) by id.
Used by: app.ask.tools.shared_core (registry assembly).
Depends on: app.ask.tools.registry, SQLAlchemy Core.
Key invariants:
  - Runs on the caller's READER-plane session; an id the caller may not see is simply
    not found (no existence leak).
  - The not-found envelope NEVER echoes the requested id: an echoed id would launder a
    model-invented uuid back into the transcript as citable evidence (cross-vendor N5).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ask.tools.registry import ToolSpec
from app.ask.tools.tool_helpers import parse_id_arg


async def _get_email(session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
    """Fetch one full message (body, recipients, attachment list) by its id."""
    email_id = parse_id_arg(args, "email_id")
    message = (
        await session.execute(
            text(
                """
                SELECT m.id, m.sent_at, m.from_address, m.from_name, m.subject,
                       m.direction, m.is_reply, m.body_text
                FROM email_message m WHERE m.id = CAST(:email_id AS uuid)
                """
            ),
            {"email_id": email_id},
        )
    ).mappings().first()
    if message is None:
        # Never echo the requested id back (anti-fabrication N5): a hallucinated id echoed
        # into the observation would launder itself into citable "tool evidence".
        return {"found": False, "note": "No email with the requested id is visible to you."}
    recipients = await session.execute(
        text(
            """
            SELECT kind, name, address FROM email_recipient
            WHERE email_id = CAST(:email_id AS uuid)
              -- BCC IS NEVER SERVED (PF-01 AC19). A stored message is the OWNER's copy; on a
              -- Sent copy it carries the blind-copy set, while a recipient's own source copy
              -- never does. Grants are issued to every addressee regardless of kind, so a
              -- plain To recipient reading this row would otherwise learn exactly who was
              -- blind-copied — the one thing BCC means. The reader plane cannot prove who the
              -- owner is, so it fails closed for everyone rather than guessing.
              AND kind IN ('to', 'cc')
            ORDER BY kind, address
            """
        ),
        {"email_id": email_id},
    )
    attachments = await session.execute(
        text(
            """
            SELECT id, filename, content_type, size_bytes, extraction_status
            FROM email_attachment WHERE email_id = CAST(:email_id AS uuid) ORDER BY filename
            """
        ),
        {"email_id": email_id},
    )
    return {
        "found": True,
        **dict(message),
        "recipients": [dict(r) for r in recipients.mappings()],
        "attachments": [dict(r) for r in attachments.mappings()],
    }


GET_EMAIL_SPEC = ToolSpec(
    name="get_email",
    description=(
        "Fetch one email in full by id: complete body text, all recipients "
        "(to/cc), and the list of attachments with types and sizes."
    ),
    parameters={
        "type": "object",
        "properties": {
            "email_id": {"type": "string", "description": "The message id (UUID)."}
        },
        "required": ["email_id"],
    },
    executor=_get_email,
)
