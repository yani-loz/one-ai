"""
Role: Pydantic request/response models for the platform connector-entitlement endpoints (CO-01
      Tier 1) — a platform admin grants/revokes which connector types a company may use.
Used by: routes.connector_entitlement_routes, services.connector_entitlement_service.
Depends on: pydantic, app.connectors.enums, app.connectors.models.connector_entitlement.
Key invariants:
  - Metadata only — entitlement carries no credential/content. extra='forbid' on the request.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.connectors.enums import ConnectorType
from app.connectors.models.connector_entitlement import ConnectorEntitlement


class SetEntitlementRequest(BaseModel):
    """Grant (enabled=True) or revoke (enabled=False) a company's entitlement to a type."""

    model_config = ConfigDict(extra="forbid")

    connector_type: ConnectorType
    enabled: bool


class EntitlementResponse(BaseModel):
    """A company's entitlement to one connector type (the plan ceiling). No secret/content."""

    model_config = ConfigDict(from_attributes=True)

    org_id: UUID
    connector_type: str
    enabled: bool
    granted_at: datetime
    revoked_at: datetime | None

    @classmethod
    def from_model(cls, entitlement: ConnectorEntitlement) -> EntitlementResponse:
        """Build the response from the ORM row."""
        return cls(
            org_id=entitlement.org_id,
            connector_type=entitlement.connector_type,
            enabled=entitlement.enabled,
            granted_at=entitlement.granted_at,
            revoked_at=entitlement.revoked_at,
        )
