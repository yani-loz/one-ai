"""
Role: Tier-1 entitlement endpoints (CO-01) — a platform admin grants/revokes which connector
      types a company may use. Routes parse + delegate + return (rule A5); logic is in
      ConnectorEntitlementService.
Used by: app.connectors.router (aggregated into connectors_router; exposed under /platform).
Depends on: connectors.dependencies (get_connector_entitlement_service — GLOBAL session),
            entitlement_schemas, identity.dependencies.get_current_platform_admin, Principal.
Key invariants:
  - Gated by get_current_platform_admin (a company token is rejected by the audience check). The
    target company is the {org_id} PATH param (a platform admin acts across orgs) — NOT the JWT.
  - Entitlement is the platform plane: the service runs on the GLOBAL session. Metadata only.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from app.connectors.dependencies import get_connector_entitlement_service
from app.connectors.schemas.entitlement_schemas import (
    EntitlementResponse,
    SetEntitlementRequest,
)
from app.connectors.services.connector_entitlement_service import ConnectorEntitlementService
from app.identity.dependencies import get_current_platform_admin
from app.identity.principal import Principal

router = APIRouter(
    prefix="/platform/orgs/{org_id}/connector-entitlements", tags=["connectors-entitlements"]
)


@router.get("", response_model=list[EntitlementResponse])
async def list_entitlements(
    org_id: UUID,
    _principal: Principal = Depends(get_current_platform_admin),
    service: ConnectorEntitlementService = Depends(get_connector_entitlement_service),
) -> list[EntitlementResponse]:
    """List a company's connector-type entitlements (the plan ceiling)."""
    entitlements = await service.list_for_org(org_id)
    return [EntitlementResponse.from_model(entitlement) for entitlement in entitlements]


@router.put("", response_model=EntitlementResponse)
async def set_entitlement(
    org_id: UUID,
    payload: SetEntitlementRequest,
    principal: Principal = Depends(get_current_platform_admin),
    service: ConnectorEntitlementService = Depends(get_connector_entitlement_service),
) -> EntitlementResponse:
    """Grant (enabled=True) or revoke (enabled=False) a company's entitlement to a type."""
    entitlement = await service.set_entitlement(
        org_id=org_id,
        connector_type=payload.connector_type,
        enabled=payload.enabled,
        actor=principal,
    )
    return EntitlementResponse.from_model(entitlement)
