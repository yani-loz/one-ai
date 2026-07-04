"""
Role: Data access for the visibility-scope flip on content rows (no business decisions — rule
      A5). The promotion service decides WHETHER a flip is allowed; this executes the flip on
      the message AND its children in one statement each (the AC22 same-transaction inheritance).
Used by: app.access.services.promotion_service.
Depends on: the email content models (string-free: SQL over the mapped tables via SQLAlchemy
      update()); app.connectors.imap.models.email.
Key invariants:
  - EVERY statement is org-scoped.
  - The flip touches visibility_scope ONLY — origin_scope is immutable (DB trigger) and the
    lineage-guard trigger validates the promotion row exists IN THE SAME TRANSACTION, so calling
    this without a staged visibility_promotion row fails loudly at the DB.
  - Children flip WITH the parent (one transaction): a promoted message's recipients and
    attachments become org-visible together — the principal-set equivalence (AC22) never breaks.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient
from app.connectors.models.connector_connection import ConnectorConnection


class ContentVisibilityRepository:
    """Org-scoped visibility flips for email content rows (message + children together)."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to the caller's tenant-scoped session."""
        self._session = session

    async def get_email_message_for_promotion(
        self, org_id: UUID, message_id: UUID
    ) -> tuple[UUID, str] | None:
        """Return (connection_id, visibility_scope) of one org's message, or None if absent.

        Takes a ROW LOCK (FOR UPDATE): concurrent promotion requests serialize here, so the
        second sees the flipped scope and is refused instead of double-recording the same
        widening (2026-07-04 review P2).
        """
        result = await self._session.execute(
            select(EmailMessage.connection_id, EmailMessage.visibility_scope)
            .where(EmailMessage.org_id == org_id, EmailMessage.id == message_id)
            .with_for_update()
        )
        row = result.first()
        return (row[0], row[1]) if row is not None else None

    async def get_connection_owner(self, org_id: UUID, connection_id: UUID) -> UUID | None:
        """The owner_user_id of one org's connection (None = shared/owner-less or absent)."""
        return (
            await self._session.execute(
                select(ConnectorConnection.owner_user_id).where(
                    ConnectorConnection.org_id == org_id,
                    ConnectorConnection.id == connection_id,
                )
            )
        ).scalar_one_or_none()

    async def promote_email_message_to_org(self, org_id: UUID, message_id: UUID) -> int:
        """Flip one message + its children to visibility_scope='org'; returns flipped MESSAGE
        row count (0 = it was already org-visible — the caller must treat that as a refusal).

        The message UPDATE carries an expected-scope predicate (belt under the FOR UPDATE lock)
        so a repeated flip is a no-op, never a second transition. The caller MUST have staged
        the visibility_promotion row in the same transaction — the DB lineage guard rejects the
        UPDATE otherwise (AC5 is DB-enforced, not trusted to this code).
        """
        result = await self._session.execute(
            update(EmailMessage)
            .where(
                EmailMessage.org_id == org_id,
                EmailMessage.id == message_id,
                EmailMessage.visibility_scope != "org",
            )
            .values(visibility_scope="org")
        )
        flipped_messages = result.rowcount or 0
        if not flipped_messages:
            return 0
        for child_model in (EmailRecipient, EmailAttachment):
            await self._session.execute(
                update(child_model)
                .where(child_model.org_id == org_id, child_model.email_id == message_id)
                .values(visibility_scope="org")
            )
        return flipped_messages
