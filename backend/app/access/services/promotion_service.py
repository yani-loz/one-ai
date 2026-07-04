"""
Role: The ONLY audience-widening path (PF-01 §6): a content OWNER's one-click promotion of a
      restricted row to org visibility — approved-by-human, audit-anchored, atomic. 'admin_widen'
      does not exist: a company admin who does not own the source connection cannot widen.
Used by: app.access.routes.promotion_routes.
Depends on: .repositories (content visibility), app.access.models.visibility_promotion,
      app.connectors.models.connector_connection (ownership check), app.identity audit service.
Key invariants:
  - OWNERS WIDEN (PF-01-S2/S4): the approver must be the owner_user_id of the message's source
    connection. A shared (owner-less) connection has no promoter — fail-closed, not admin-open.
  - ATOMIC LINEAGE (AC5/AC13): audit row → visibility_promotion row → scope flip, all in ONE
    transaction; the DB lineage-guard trigger independently rejects a flip whose promotion row
    is missing, so this service cannot be bypassed by future code either.
  - Cross-tenant / not-found probes raise ContentObjectNotFoundError (→ 404, never an
    existence leak); non-owner same-org probes raise PromotionNotAllowedError (→ 403).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.access.exceptions import ContentObjectNotFoundError, PromotionNotAllowedError
from app.access.models.visibility_promotion import VisibilityPromotion
from app.access.repositories.content_visibility_repository import ContentVisibilityRepository
from app.identity.enums import AuditActorType
from app.identity.services.audit_service import AuditAction, AuditEvent, AuditService


class PromotionService:
    """Owner-approved widening of restricted content to org visibility (atomic + audited)."""

    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        """Bind to the caller's tenant session + the audit writer on the SAME session."""
        self._session = session
        self._content = ContentVisibilityRepository(session)
        self._audit = audit

    async def promote_email_message(
        self, org_id: UUID, approver_user_id: UUID, approver_email: str | None, message_id: UUID
    ) -> UUID:
        """Promote one restricted email message (+children) to org visibility; returns the
        visibility_promotion row id.

        Raises:
            ContentObjectNotFoundError: no such message in this org (or already outside it).
            PromotionNotAllowedError: the approver does not own the source connection, or the
                message is already org-visible (nothing to widen).
        """
        located = await self._content.get_email_message_for_promotion(org_id, message_id)
        if located is None:
            raise ContentObjectNotFoundError(f"email_message {message_id} not found in org")
        connection_id, visibility_scope = located
        if visibility_scope == "org":
            raise PromotionNotAllowedError("message is already org-visible")

        owner_user_id = await self._content.get_connection_owner(org_id, connection_id)
        if owner_user_id is None or owner_user_id != approver_user_id:
            # Owner-less (shared) connections have NO promoter — fail-closed, never admin-open.
            raise PromotionNotAllowedError("only the source connection's owner may widen")

        audit_log_id = await self._audit.record(
            AuditEvent(
                action=AuditAction.VISIBILITY_PROMOTED,
                actor_type=AuditActorType.user,
                actor_id=approver_user_id,
                actor_email=approver_email,
                org_id=org_id,
                entity_type="email_message",
                entity_id=message_id,
                details={"from_scope": "restricted", "to_scope": "org"},
            )
        )
        promotion = VisibilityPromotion(
            org_id=org_id,
            object_type="email_message",
            object_id=message_id,
            approved_by_user_id=approver_user_id,
            audit_log_id=audit_log_id,
        )
        self._session.add(promotion)
        await self._session.flush()  # the lineage row must exist BEFORE the flip (AC5 trigger)
        flipped = await self._content.promote_email_message_to_org(org_id, message_id)
        if not flipped:  # belt under the FOR UPDATE lock: never record a second transition
            raise PromotionNotAllowedError("message is already org-visible")
        return promotion.id
