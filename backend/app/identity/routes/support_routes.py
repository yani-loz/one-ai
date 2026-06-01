"""
Role: Break-glass support-access endpoints — the platform side (request / list-mine /
      revoke, aud='platform') and the company side (inbox / approve / deny / revoke,
      company_admin). Routes parse + delegate + return (rule A5).
Used by: app.identity.router (both routers aggregated into identity_router).
Depends on: identity.dependencies (gates + the two support-service providers),
            identity.schemas.support_schemas, the two support services, identity.principal.
Key invariants:
  - SEPARATE DOMAINS: the platform router is gated by get_current_platform_admin
    (aud='platform' — a company token is rejected) and the company router by
    require_company_admin (aud='company'). Approval lives ONLY on the company router —
    no platform endpoint can approve (consent).
  - The company endpoints pass principal.org_id (from the verified JWT) to the service, so
    a company_admin only ever acts on their own org's grants (cross-org → 404).
  - Metadata only — a grant is a consent/lifecycle record, never tenant content.
  - No business logic here.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.identity.dependencies import (
    get_company_support_service,
    get_current_platform_admin,
    get_platform_support_service,
    require_company_admin,
)
from app.identity.principal import Principal
from app.identity.schemas.support_schemas import (
    SupportAccessRequest,
    SupportGrantResponse,
)
from app.identity.services.company_support_service import CompanySupportService
from app.identity.services.platform_support_service import PlatformSupportService

platform_support_router = APIRouter(prefix="/platform", tags=["platform-support"])
company_support_router = APIRouter(prefix="/support-access", tags=["support-access"])


# — Platform side: a platform admin requests time-boxed access to a tenant —


@platform_support_router.post(
    "/orgs/{org_id}/support-requests",
    response_model=SupportGrantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def request_support_access(
    org_id: UUID,
    payload: SupportAccessRequest,
    admin: Principal = Depends(get_current_platform_admin),
    service: PlatformSupportService = Depends(get_platform_support_service),
) -> SupportGrantResponse:
    """Open a break-glass request for one company (awaits the customer's approval)."""
    return await service.request_access(org_id, payload.reason, admin)


@platform_support_router.get(
    "/support-requests", response_model=list[SupportGrantResponse]
)
async def list_my_support_requests(
    admin: Principal = Depends(get_current_platform_admin),
    service: PlatformSupportService = Depends(get_platform_support_service),
) -> list[SupportGrantResponse]:
    """List the break-glass requests this platform admin has made (newest-first)."""
    return await service.list_my_requests(admin)


@platform_support_router.post(
    "/support-requests/{grant_id}/revoke", response_model=SupportGrantResponse
)
async def revoke_my_support_request(
    grant_id: UUID,
    admin: Principal = Depends(get_current_platform_admin),
    service: PlatformSupportService = Depends(get_platform_support_service),
) -> SupportGrantResponse:
    """Revoke a break-glass request this admin made (from requested or approved)."""
    return await service.revoke(grant_id, admin)


# — Company side: a company_admin reviews + decides requests targeting their org —


@company_support_router.get("", response_model=list[SupportGrantResponse])
async def list_org_support_requests(
    principal: Principal = Depends(require_company_admin),
    service: CompanySupportService = Depends(get_company_support_service),
) -> list[SupportGrantResponse]:
    """List the support-access requests targeting the caller's org (the approval inbox)."""
    return await service.list_for_org(principal.org_id)


@company_support_router.post("/{grant_id}/approve", response_model=SupportGrantResponse)
async def approve_support_request(
    grant_id: UUID,
    principal: Principal = Depends(require_company_admin),
    service: CompanySupportService = Depends(get_company_support_service),
) -> SupportGrantResponse:
    """Approve a support-access request for the caller's org (opens the time box)."""
    return await service.approve(grant_id, principal.org_id, principal)


@company_support_router.post("/{grant_id}/deny", response_model=SupportGrantResponse)
async def deny_support_request(
    grant_id: UUID,
    principal: Principal = Depends(require_company_admin),
    service: CompanySupportService = Depends(get_company_support_service),
) -> SupportGrantResponse:
    """Deny a support-access request for the caller's org."""
    return await service.deny(grant_id, principal.org_id, principal)


@company_support_router.post("/{grant_id}/revoke", response_model=SupportGrantResponse)
async def revoke_support_request(
    grant_id: UUID,
    principal: Principal = Depends(require_company_admin),
    service: CompanySupportService = Depends(get_company_support_service),
) -> SupportGrantResponse:
    """Revoke an active/pending support grant for the caller's org (cuts off access)."""
    return await service.revoke(grant_id, principal.org_id, principal)
