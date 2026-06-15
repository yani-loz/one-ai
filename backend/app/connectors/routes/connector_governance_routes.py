"""
Role: Tier-2 governance endpoints (CO-01) — a company admin reads the §7 metadata-only health
      roll-up and sets the org-wide policy + per-user grant/deny overrides. Routes parse +
      delegate + return (rule A5); all logic is in ConnectorGovernanceService.
Used by: app.connectors.router (aggregated BEFORE the admin CRUD router so the literal
         /governance|/policies|/overrides paths win over /admin/connectors/{connection_id}).
Depends on: connectors.dependencies (get_connector_governance_service), governance_schemas,
            connectors.enums, identity.dependencies.require_company_admin, identity.Principal.
Key invariants:
  - Gated by require_company_admin (member -> 403); org scope from principal.org_id (the verified
    JWT). Cross-org reads/writes can't see another org (tenant session + RLS).
  - §7 / §8: the governance view returns ConnectionMetadataResponse only — owner + health, never a
    credential or email content. The admin cannot enable a type beyond the company's entitlement.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from app.connectors.dependencies import get_connector_governance_service
from app.connectors.enums import ConnectorType
from app.connectors.schemas.governance_schemas import (
    ConnectorGovernanceResponse,
    SetOverrideRequest,
    SetPolicyRequest,
)
from app.connectors.services.connector_governance_service import ConnectorGovernanceService
from app.identity.dependencies import require_company_admin
from app.identity.principal import Principal

router = APIRouter(prefix="/admin/connectors", tags=["connectors-governance"])


@router.get("/governance/{connector_type}", response_model=ConnectorGovernanceResponse)
async def get_governance(
    connector_type: ConnectorType,
    principal: Principal = Depends(require_company_admin),
    service: ConnectorGovernanceService = Depends(get_connector_governance_service),
) -> ConnectorGovernanceResponse:
    """Return the governance view for a type: ceiling, org-wide policy, overrides, and health."""
    return await service.get_governance(principal.org_id, connector_type)


@router.put("/policies", response_model=ConnectorGovernanceResponse)
async def set_org_wide_policy(
    payload: SetPolicyRequest,
    principal: Principal = Depends(require_company_admin),
    service: ConnectorGovernanceService = Depends(get_connector_governance_service),
) -> ConnectorGovernanceResponse:
    """Set the org-wide enable/disable for a connector type (403 if enabling beyond entitlement)."""
    return await service.set_org_wide(
        org_id=principal.org_id,
        connector_type=payload.connector_type,
        org_wide_enabled=payload.org_wide_enabled,
        actor=principal,
    )


@router.put("/overrides", response_model=ConnectorGovernanceResponse)
async def set_user_override(
    payload: SetOverrideRequest,
    principal: Principal = Depends(require_company_admin),
    service: ConnectorGovernanceService = Depends(get_connector_governance_service),
) -> ConnectorGovernanceResponse:
    """Grant or deny a connector type to one user (403 if granting beyond entitlement)."""
    return await service.set_override(
        org_id=principal.org_id,
        user_id=payload.user_id,
        connector_type=payload.connector_type,
        override_type=payload.override_type,
        actor=principal,
    )


@router.delete("/overrides/{connector_type}/{user_id}", response_model=ConnectorGovernanceResponse)
async def clear_user_override(
    connector_type: ConnectorType,
    user_id: UUID,
    principal: Principal = Depends(require_company_admin),
    service: ConnectorGovernanceService = Depends(get_connector_governance_service),
) -> ConnectorGovernanceResponse:
    """Remove a user's override (reverts them to the org-wide policy)."""
    return await service.clear_override(
        org_id=principal.org_id,
        user_id=user_id,
        connector_type=connector_type,
        actor=principal,
    )
