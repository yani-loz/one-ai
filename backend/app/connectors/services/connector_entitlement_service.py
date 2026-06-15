"""
Role: Tier-1 entitlement orchestration (CO-01) — a platform admin grants/revokes which connector
      types a company may use (the plan ceiling). Holds the business logic (rule A5).
Used by: routes.connector_entitlement_routes; constructed on the GLOBAL session (platform plane).
Depends on: ConnectorEntitlementRepository (global), identity.services.audit_service, identity
            Principal/AuditActorType, connectors enums.
Key invariants:
  - PLATFORM PLANE: every method runs on the BYPASSRLS global session — entitlement is not a
    tenant table. The platform admin (actor) is verified upstream (get_current_platform_admin).
  - AUDITED: grant/revoke record entitlement.granted / entitlement.revoked same-tx, org-scoped to
    the target company, actor-attributed to the platform admin. Metadata only (type + enabled).
  - No cascade on revoke: policies/connections persist and are re-exposed on re-grant (AC7).
"""

from __future__ import annotations

from uuid import UUID

from app.connectors.enums import ConnectorType
from app.connectors.models.connector_entitlement import ConnectorEntitlement
from app.connectors.repositories.connector_entitlement_repository import (
    ConnectorEntitlementRepository,
)
from app.identity.enums import AuditActorType
from app.identity.principal import Principal
from app.identity.services.audit_service import AuditAction, AuditEvent, AuditService

_ENTITY_ENTITLEMENT = "connector_entitlement"


class ConnectorEntitlementService:
    """Grant / revoke / read a company's connector-type entitlements (platform-managed)."""

    def __init__(self, entitlements: ConnectorEntitlementRepository, audit: AuditService) -> None:
        """Wire the entitlement repository (global session) and the audit writer."""
        self._entitlements = entitlements
        self._audit = audit

    async def list_for_org(self, org_id: UUID) -> list[ConnectorEntitlement]:
        """Return all of a company's entitlement rows (one per connector type)."""
        return await self._entitlements.list_for_org(org_id)

    async def set_entitlement(
        self,
        *,
        org_id: UUID,
        connector_type: ConnectorType,
        enabled: bool,
        actor: Principal,
    ) -> ConnectorEntitlement:
        """Grant (enabled=True) or revoke (enabled=False) a company's entitlement to a type.

        Upserts the row and records entitlement.granted / entitlement.revoked same-tx (org-scoped
        to the target company, attributed to the platform admin). Policies/connections are left
        intact (re-exposed on re-grant).
        """
        entitlement = await self._entitlements.upsert(
            org_id=org_id,
            connector_type=connector_type.value,
            enabled=enabled,
            set_by_platform_admin_id=actor.subject_id,
        )
        action = AuditAction.ENTITLEMENT_GRANTED if enabled else AuditAction.ENTITLEMENT_REVOKED
        await self._audit.record(
            AuditEvent(
                action=action,
                actor_type=AuditActorType.platform_admin,
                actor_id=actor.subject_id,
                org_id=org_id,
                entity_type=_ENTITY_ENTITLEMENT,
                entity_id=entitlement.id,
                details={"connector_type": connector_type.value, "enabled": enabled},
            )
        )
        return entitlement
