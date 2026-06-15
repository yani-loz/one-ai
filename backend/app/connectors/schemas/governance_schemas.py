"""
Role: Pydantic models for the company-admin connector GOVERNANCE endpoints (CO-01 Tier 2) —
      org-wide enable/disable, per-user grant/deny, and the §7 metadata-only connection health
      roll-up. The admin governs reach + sees health; never a credential, never email content.
Used by: routes.connector_governance_routes, services.connector_governance_service.
Depends on: pydantic, app.connectors.enums, app.connectors.models (connector_connection,
            connector_policy, connector_policy_override).
Key invariants:
  - §7 / §8 METADATA-ONLY: ConnectionMetadataResponse exposes owner + type + status + sync health
    only — NO secret, NO host/port/username (the mailbox params are the owner's), NO email content.
    This is the structural enforcement of "admins see knowledge, not conversations".
  - extra='forbid' on every request. The org-entitlement ceiling is surfaced (`entitled`) so the
    admin UI can't offer to enable a type the company isn't entitled to.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.connectors.enums import ConnectorType, OverrideType
from app.connectors.models.connector_connection import ConnectorConnection
from app.connectors.models.connector_policy import ConnectorPolicy
from app.connectors.models.connector_policy_override import ConnectorPolicyOverride


class SetPolicyRequest(BaseModel):
    """Set a company's org-wide enable/disable for a connector type."""

    model_config = ConfigDict(extra="forbid")

    connector_type: ConnectorType
    org_wide_enabled: bool


class SetOverrideRequest(BaseModel):
    """Set a per-user grant/deny override for a connector type (admin governance)."""

    model_config = ConfigDict(extra="forbid")

    connector_type: ConnectorType
    user_id: UUID
    override_type: OverrideType


class OverrideResponse(BaseModel):
    """One per-user override row (metadata only)."""

    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    connector_type: str
    override_type: str

    @classmethod
    def from_model(cls, override: ConnectorPolicyOverride) -> OverrideResponse:
        """Build the response from the ORM row."""
        return cls(
            user_id=override.user_id,
            connector_type=override.connector_type,
            override_type=override.override_type,
        )


class ConnectionMetadataResponse(BaseModel):
    """§7 metadata-only view of a connection for admin/governance — no secret, content, or params.

    Deliberately excludes username/host/port (the owner's mailbox params) and every secret/content
    field. Carries owner + type + lifecycle + sync health so an admin can see WHO has connected and
    whether it's syncing, never the inbox.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    connector_type: str
    owner_user_id: UUID | None
    status: str
    is_enabled: bool
    sync_status: str
    synced_count: int
    total_count: int | None
    last_synced_at: datetime | None
    last_error: str | None

    @classmethod
    def from_model(cls, connection: ConnectorConnection) -> ConnectionMetadataResponse:
        """Build the metadata-only view from the ORM row (no secret/content/params copied)."""
        return cls(
            id=connection.id,
            connector_type=connection.connector_type,
            owner_user_id=connection.owner_user_id,
            status=connection.status,
            is_enabled=connection.disabled_at is None,
            sync_status=connection.sync_status,
            synced_count=connection.synced_count,
            total_count=connection.total_count,
            last_synced_at=connection.last_synced_at,
            last_error=connection.last_error,
        )


class ConnectorGovernanceResponse(BaseModel):
    """The full governance view for one connector type: entitlement ceiling + policy + overrides."""

    model_config = ConfigDict(from_attributes=True)

    connector_type: str
    entitled: bool
    org_wide_enabled: bool
    overrides: list[OverrideResponse]
    connections: list[ConnectionMetadataResponse]

    @classmethod
    def build(
        cls,
        *,
        connector_type: str,
        entitled: bool,
        policy: ConnectorPolicy | None,
        overrides: list[ConnectorPolicyOverride],
        connections: list[ConnectorConnection],
    ) -> ConnectorGovernanceResponse:
        """Assemble the governance view from the entitlement, policy, overrides, and connections."""
        return cls(
            connector_type=connector_type,
            entitled=entitled,
            org_wide_enabled=policy.org_wide_enabled if policy is not None else False,
            overrides=[OverrideResponse.from_model(o) for o in overrides],
            connections=[ConnectionMetadataResponse.from_model(c) for c in connections],
        )
