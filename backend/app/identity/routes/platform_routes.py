"""
Role: Platform-admin endpoints (/platform/*) — SEPARATE auth domain. Routes parse +
      delegate + return (A5).
Used by: app.identity.router (aggregated into identity_router).
Depends on: identity.dependencies (PlatformAuthService provider, platform-admin gate),
            identity.schemas.platform_schemas, identity.schemas.auth_schemas,
            identity.services.platform_auth_service.
Key invariants:
  - /platform/login, /platform/refresh and /platform/logout are public (they present or
    issue tokens directly). /platform/me and /platform/orgs require a verified platform
    token (aud='platform') via get_current_platform_admin — a company token is rejected
    with 401, and vice versa on company endpoints.
  - Refresh is single-use rotation scoped to subject_type='platform_admin': a company
    refresh token presented to /platform/refresh is rejected (401, domain mismatch).
  - GET /platform/orgs returns METADATA ONLY (no tenant content); GET /platform/me returns
    the admin's own identity only.
  - No business logic here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.identity.dependencies import (
    get_current_platform_admin,
    get_platform_auth_service,
)
from app.identity.principal import Principal
from app.identity.schemas.auth_schemas import (
    LogoutRequest,
    RefreshRequest,
    TokenPairResponse,
)
from app.identity.schemas.platform_schemas import (
    OrganizationCreateRequest,
    OrganizationOnboardedResponse,
    OrganizationResponse,
    PlatformAdminResponse,
    PlatformLoginRequest,
)
from app.identity.services.platform_auth_service import PlatformAuthService

router = APIRouter(prefix="/platform", tags=["platform"])


@router.post(
    "/login", response_model=TokenPairResponse, response_model_exclude_none=True
)
async def platform_login(
    payload: PlatformLoginRequest,
    service: PlatformAuthService = Depends(get_platform_auth_service),
) -> TokenPairResponse:
    """Authenticate a platform admin; return access+refresh tokens (aud='platform').

    Excludes the null `user` field so the body is exactly {access_token,
    refresh_token, token_type} per SPEC §4 (the platform domain has no user view).
    """
    access_token, refresh_token = await service.login(payload.email, payload.password)
    return TokenPairResponse(access_token=access_token, refresh_token=refresh_token)


@router.post(
    "/refresh", response_model=TokenPairResponse, response_model_exclude_none=True
)
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
    _admin: Principal = Depends(get_current_platform_admin),
    service: PlatformAuthService = Depends(get_platform_auth_service),
) -> OrganizationOnboardedResponse:
    """Create a new organization and its first company_admin atomically."""
    return await service.onboard_organization(payload)


@router.get("/orgs", response_model=list[OrganizationResponse])
async def list_organizations(
    _admin: Principal = Depends(get_current_platform_admin),
    service: PlatformAuthService = Depends(get_platform_auth_service),
) -> list[OrganizationResponse]:
    """List all organizations as metadata (no tenant content)."""
    return await service.list_organizations()
