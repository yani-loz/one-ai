"""
Role: GDPR erasure + compliance-export endpoints (platform domain). Routes parse +
      delegate + return (rule A5).
Used by: app.identity.router (aggregated into identity_router).
Depends on: identity.dependencies (platform gate + erasure-service provider),
            identity.schemas.erasure_schemas, services.erasure_service, identity.principal.
Key invariants:
  - Platform-gated (get_current_platform_admin) — a company token is rejected (401). Erasure
    is an offboarding control, not a tenant self-service action.
  - DELETE-equivalent erasure is a POST carrying a reason + slug confirmation (the body
    guards an irreversible op); the route returns the deletion certificate.
  - Metadata only — the certificate + export describe counts/actions, never tenant content.
  - No business logic here.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from app.identity.dependencies import get_current_platform_admin, get_erasure_service
from app.identity.principal import Principal
from app.identity.schemas.erasure_schemas import (
    ComplianceExportResponse,
    ErasureCertificateResponse,
    ErasureRequest,
)
from app.identity.services.erasure_service import ErasureService

router = APIRouter(prefix="/platform", tags=["platform-erasure"])


@router.post(
    "/orgs/{org_id}/erase", response_model=ErasureCertificateResponse
)
async def erase_organization(
    org_id: UUID,
    payload: ErasureRequest,
    admin: Principal = Depends(get_current_platform_admin),
    service: ErasureService = Depends(get_erasure_service),
) -> ErasureCertificateResponse:
    """Erase a tenant's personal data (legal-hold permitting); return the deletion certificate."""
    return await service.erase_organization(org_id, payload, admin)


@router.get(
    "/orgs/{org_id}/compliance-export", response_model=ComplianceExportResponse
)
async def export_organization_compliance(
    org_id: UUID,
    _admin: Principal = Depends(get_current_platform_admin),
    service: ErasureService = Depends(get_erasure_service),
) -> ComplianceExportResponse:
    """Return the compliance export for one org — metadata + its full audit trail."""
    return await service.export_compliance(org_id)
