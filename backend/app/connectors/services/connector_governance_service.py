"""
Role: Tier-2 governance orchestration (CO-01) — a company admin sets the org-wide policy and
      per-user grant/deny overrides, and reads the §7 metadata-only health roll-up. Business
      logic (rule A5).
Used by: routes.connector_governance_routes; constructed on the caller's TENANT session, plus a
         GLOBAL-session entitlement reader for the Tier-1 ceiling check.
Depends on: ConnectorPolicyRepository / ConnectorPolicyOverrideRepository / ConnectorConnection
            Repository (tenant), ConnectorEntitlementRepository (global), audit_service, enums,
            exceptions, governance_schemas.
Key invariants:
  - ENTITLEMENT CEILING (AC6): an admin can never ENABLE org-wide or GRANT a user a type the
    company is not entitled to → ConnectorNotEntitledError (-> 403). Disabling/denying is always
    allowed (you can always restrict within your plan).
  - §7 METADATA-ONLY (AC8): the health roll-up returns ConnectionMetadataResponse (owner + type +
    status + sync health) — never a credential, never email content, never mailbox params.
  - CROSS-TENANT: every read/write is org-scoped (tenant session + org_id filter; RLS backstop).
  - AUDITED: policy + override writes record connector.policy_changed same-tx, actor-attributed.
"""

from __future__ import annotations

from uuid import UUID

from app.connectors.enums import ConnectorType, OverrideType
from app.connectors.exceptions import ConnectorNotEntitledError
from app.connectors.repositories.connector_connection_repository import (
    ConnectorConnectionRepository,
)
from app.connectors.repositories.connector_entitlement_repository import (
    ConnectorEntitlementRepository,
)
from app.connectors.repositories.connector_policy_override_repository import (
    ConnectorPolicyOverrideRepository,
)
from app.connectors.repositories.connector_policy_repository import ConnectorPolicyRepository
from app.connectors.schemas.governance_schemas import ConnectorGovernanceResponse
from app.identity.enums import AuditActorType
from app.identity.principal import Principal
from app.identity.services.audit_service import AuditAction, AuditEvent, AuditService

_ENTITY_POLICY = "connector_policy"


class ConnectorGovernanceService:
    """Company-admin governance: org-wide policy + per-user overrides + metadata-only health."""

    def __init__(
        self,
        *,
        policies: ConnectorPolicyRepository,
        overrides: ConnectorPolicyOverrideRepository,
        connections: ConnectorConnectionRepository,
        entitlements: ConnectorEntitlementRepository,
        audit: AuditService,
    ) -> None:
        """Wire the tenant repos (policy/override/connection), the entitlement reader, and audit."""
        self._policies = policies
        self._overrides = overrides
        self._connections = connections
        self._entitlements = entitlements
        self._audit = audit

    async def get_governance(
        self, org_id: UUID, connector_type: ConnectorType
    ) -> ConnectorGovernanceResponse:
        """Return the full governance view for a type: ceiling, policy, overrides, and health.

        The connections are the org's rows as §7 metadata only (owner + status + sync health) — the
        admin sees WHO connected and whether it syncs, never the inbox.
        """
        entitled = await self._entitlements.is_entitled(org_id, connector_type.value)
        policy = await self._policies.get(org_id, connector_type.value)
        overrides = await self._overrides.list_for_type(org_id, connector_type.value)
        connections = [
            connection
            for connection in await self._connections.list_for_org(org_id)
            if connection.connector_type == connector_type.value
        ]
        return ConnectorGovernanceResponse.build(
            connector_type=connector_type.value,
            entitled=entitled,
            policy=policy,
            overrides=overrides,
            connections=connections,
        )

    async def set_org_wide(
        self,
        *,
        org_id: UUID,
        connector_type: ConnectorType,
        org_wide_enabled: bool,
        actor: Principal,
    ) -> ConnectorGovernanceResponse:
        """Set the org-wide enable/disable for a type (AC6: can't enable beyond entitlement)."""
        if org_wide_enabled:
            await self._require_entitled(org_id, connector_type)
        await self._policies.upsert(
            org_id=org_id,
            connector_type=connector_type.value,
            org_wide_enabled=org_wide_enabled,
            set_by_user_id=actor.subject_id,
        )
        await self._record_policy_change(
            org_id=org_id,
            actor=actor,
            details={"connector_type": connector_type.value, "org_wide_enabled": org_wide_enabled},
        )
        return await self.get_governance(org_id, connector_type)

    async def set_override(
        self,
        *,
        org_id: UUID,
        user_id: UUID,
        connector_type: ConnectorType,
        override_type: OverrideType,
        actor: Principal,
    ) -> ConnectorGovernanceResponse:
        """Set a per-user grant/deny override (AC6: granting requires entitlement)."""
        if override_type is OverrideType.grant:
            await self._require_entitled(org_id, connector_type)
        await self._overrides.upsert(
            org_id=org_id,
            user_id=user_id,
            connector_type=connector_type.value,
            override_type=override_type,
            set_by_user_id=actor.subject_id,
        )
        await self._record_policy_change(
            org_id=org_id,
            actor=actor,
            details={
                "connector_type": connector_type.value,
                "user_id": str(user_id),
                "override_type": override_type.value,
            },
        )
        return await self.get_governance(org_id, connector_type)

    async def clear_override(
        self,
        *,
        org_id: UUID,
        user_id: UUID,
        connector_type: ConnectorType,
        actor: Principal,
    ) -> ConnectorGovernanceResponse:
        """Remove a user's override (reverts them to the org-wide policy)."""
        await self._overrides.delete(org_id, user_id, connector_type.value)
        await self._record_policy_change(
            org_id=org_id,
            actor=actor,
            details={
                "connector_type": connector_type.value,
                "user_id": str(user_id),
                "override_type": "cleared",
            },
        )
        return await self.get_governance(org_id, connector_type)

    async def _require_entitled(self, org_id: UUID, connector_type: ConnectorType) -> None:
        """Raise ConnectorNotEntitledError (-> 403) unless the company is entitled to the type."""
        if not await self._entitlements.is_entitled(org_id, connector_type.value):
            raise ConnectorNotEntitledError(
                "This connector is not included in your company's plan."
            )

    async def _record_policy_change(
        self, *, org_id: UUID, actor: Principal, details: dict[str, object]
    ) -> None:
        """Record a connector.policy_changed audit row same-tx (actor-attributed, content-blind)."""
        await self._audit.record(
            AuditEvent(
                action=AuditAction.CONNECTOR_POLICY_CHANGED,
                actor_type=AuditActorType(actor.subject_type),
                actor_id=actor.subject_id,
                org_id=org_id,
                entity_type=_ENTITY_POLICY,
                details=details,
            )
        )
