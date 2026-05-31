"""
Role: Platform-admin endpoints (/platform/*) — SEPARATE auth domain. Routes parse +
      delegate + return (A5).
Used by: app.identity.router (aggregated into identity_router).
Depends on: identity.dependencies (PlatformAuthService provider, platform-admin gate),
            identity.schemas.platform_schemas, identity.schemas.auth_schemas,
            identity.services.platform_auth_service.
Key invariants:
  - /platform/login is public (it issues the platform token). /platform/orgs require a
    verified platform token (aud='platform') via get_current_platform_admin — a company
    token is rejected with 401, and vice versa on company endpoints.
  - GET /platform/orgs returns METADATA ONLY (no tenant content).
  - No business logic here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.identity.dependencies import (
    get_current_platform_admin,
    get_platform_auth_service,
)
from app.identity.principal import Principal
from app.identity.schemas.auth_schemas import TokenPairResponse
from app.identity.schemas.platform_schemas import (
    OrganizationCreateRequest,
    OrganizationOnboardedResponse,
    OrganizationResponse,
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
