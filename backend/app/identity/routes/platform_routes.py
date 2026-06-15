"""
Role: Platform-admin endpoints (/platform/*) — SEPARATE auth domain. Routes parse +
      delegate + return (A5).
Used by: app.identity.router (aggregated into identity_router).
Depends on: identity.dependencies (PlatformAuthService + PlatformOrgService providers,
            platform-admin gate), identity.schemas.platform_schemas,
            identity.schemas.auth_schemas, identity.services.platform_auth_service,
            identity.services.platform_org_service.
Key invariants:
  - /platform/login, /platform/refresh and /platform/logout are public (they present or
    issue tokens directly). /platform/me and /platform/orgs require a verified platform
    token (aud='platform') via get_current_platform_admin — a company token is rejected
    with 401, and vice versa on company endpoints.
  - Refresh is single-use rotation scoped to subject_type='platform_admin': a company
    refresh token presented to /platform/refresh is rejected (401, domain mismatch).
  - GET /platform/orgs[/{id}] returns METADATA ONLY (no tenant content); the status +
    legal-hold PATCHes are metadata lifecycle, not content. GET /platform/me returns the
    admin's own identity only.
  - No business logic here.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from app.identity.dependencies import (
    get_audit_service,
    get_current_platform_admin,
    get_platform_auth_service,
    get_platform_org_service,
)
from app.identity.principal import Principal
from app.identity.routes.cookies import client_ip_from_request
from app.identity.schemas.audit_schemas import (
    DEFAULT_AUDIT_PAGE_SIZE,
    MAX_AUDIT_PAGE_SIZE,
    AuditLogEntryResponse,
)
from app.identity.schemas.auth_schemas import (
    LogoutRequest,
    RefreshRequest,
    TokenPairResponse,
)
from app.identity.schemas.platform_schemas import (
    LegalHoldUpdateRequest,
    OrganizationCreateRequest,
    OrganizationDetailResponse,
    OrganizationOnboardedResponse,
    OrganizationResponse,
    OrganizationStatusUpdateRequest,
    PlatformAdminResponse,
    PlatformLoginRequest,
)
from app.identity.services.audit_service import AuditService
from app.identity.services.platform_auth_service import PlatformAuthService
from app.identity.services.platform_org_service import PlatformOrgService

router = APIRouter(prefix="/platform", tags=["platform"])


@router.post("/login", response_model=TokenPairResponse, response_model_exclude_none=True)
async def platform_login(
    payload: PlatformLoginRequest,
    request: Request,
    service: PlatformAuthService = Depends(get_platform_auth_service),
) -> TokenPairResponse:
    """Authenticate a platform admin; return access+refresh tokens (aud='platform').

    Passes the client IP into the service for the pre-bcrypt throttle (N-01). Excludes the
    null `user` field so the body is exactly {access_token, refresh_token, token_type} per
    SPEC §4 (the platform domain has no user view; its refresh token stays in the body —
    the client holds it IN MEMORY only — and is deliberately NOT moved to a cookie).
    """
    access_token, refresh_token = await service.login(
        payload.email, payload.password, client_ip_from_request(request)
    )
    return TokenPairResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenPairResponse, response_model_exclude_none=True)
async def platform_refresh(
    payload: RefreshRequest,
    service: PlatformAuthService = Depends(get_platform_auth_service),
) -> TokenPairResponse:
    """Rotate a platform refresh token; return a brand-new access+refresh pair.

    Single-use rotation in the platform domain (subject_type='platform_admin'): a company
    refresh token presented here is rejected with 401 (domain mismatch), and the old
    platform token is revoked. Excludes the null `user` field (no user in this domain).
    """
    access_token, refresh_token = await service.refresh(payload.refresh_token)
    return TokenPairResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def platform_logout(
    payload: LogoutRequest,
    service: PlatformAuthService = Depends(get_platform_auth_service),
) -> None:
    """Revoke the presented platform refresh token (idempotent)."""
    await service.logout(payload.refresh_token)


@router.get("/me", response_model=PlatformAdminResponse)
async def read_current_platform_admin(
    admin: Principal = Depends(get_current_platform_admin),
    service: PlatformAuthService = Depends(get_platform_auth_service),
) -> PlatformAdminResponse:
    """Return the authenticated platform admin resolved from the verified token.

    A company token (aud='company') is rejected by get_current_platform_admin (401), so
    this only ever returns the platform admin named in a valid platform access token.
    """
    return await service.build_admin_view_by_id(admin.subject_id)


@router.post(
    "/orgs",
    response_model=OrganizationOnboardedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def onboard_organization(
    payload: OrganizationCreateRequest,
    admin: Principal = Depends(get_current_platform_admin),
    service: PlatformAuthService = Depends(get_platform_auth_service),
) -> OrganizationOnboardedResponse:
    """Create a new organization and its first company_admin atomically (audited)."""
    return await service.onboard_organization(payload, admin)


@router.get("/orgs", response_model=list[OrganizationResponse])
async def list_organizations(
    _admin: Principal = Depends(get_current_platform_admin),
    service: PlatformAuthService = Depends(get_platform_auth_service),
) -> list[OrganizationResponse]:
    """List all organizations as metadata (no tenant content)."""
    return await service.list_organizations()


@router.get("/orgs/{org_id}", response_model=OrganizationDetailResponse)
async def get_organization_detail(
    org_id: UUID,
    _admin: Principal = Depends(get_current_platform_admin),
    service: PlatformOrgService = Depends(get_platform_org_service),
) -> OrganizationDetailResponse:
    """Return one organization's lifecycle detail (metadata + legal hold)."""
    return await service.get_detail(org_id)


@router.patch("/orgs/{org_id}/status", response_model=OrganizationDetailResponse)
async def update_organization_status(
    org_id: UUID,
    payload: OrganizationStatusUpdateRequest,
    admin: Principal = Depends(get_current_platform_admin),
    service: PlatformOrgService = Depends(get_platform_org_service),
) -> OrganizationDetailResponse:
    """Set an organization's lifecycle status (suspend/reactivate/onboarding/offboarded)."""
    return await service.set_status(org_id, payload.status, admin)


@router.patch("/orgs/{org_id}/legal-hold", response_model=OrganizationDetailResponse)
async def update_organization_legal_hold(
    org_id: UUID,
    payload: LegalHoldUpdateRequest,
    admin: Principal = Depends(get_current_platform_admin),
    service: PlatformOrgService = Depends(get_platform_org_service),
) -> OrganizationDetailResponse:
    """Set or clear an organization's legal hold."""
    return await service.set_legal_hold(org_id, payload.legal_hold, admin)


@router.get("/orgs/{org_id}/audit", response_model=list[AuditLogEntryResponse])
async def get_organization_audit(
    org_id: UUID,
    limit: int = Query(DEFAULT_AUDIT_PAGE_SIZE, ge=1, le=MAX_AUDIT_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    _admin: Principal = Depends(get_current_platform_admin),
    service: AuditService = Depends(get_audit_service),
) -> list[AuditLogEntryResponse]:
    """Return one organization's audit trail (newest-first, paginated, metadata only)."""
    return await service.list_for_org(org_id, limit=limit, offset=offset)


@router.get("/audit", response_model=list[AuditLogEntryResponse])
async def get_global_audit(
    action: str | None = Query(None, max_length=64),
    org_id: UUID | None = Query(None),
    limit: int = Query(DEFAULT_AUDIT_PAGE_SIZE, ge=1, le=MAX_AUDIT_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    _admin: Principal = Depends(get_current_platform_admin),
    service: AuditService = Depends(get_audit_service),
) -> list[AuditLogEntryResponse]:
    """Return the global audit trail, optionally filtered (newest-first, paginated)."""
    return await service.list_global(action=action, org_id=org_id, limit=limit, offset=offset)
