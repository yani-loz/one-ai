"""
Role: Company-user auth endpoints (/auth/*). Routes parse + delegate + return (A5).
Used by: app.identity.router (aggregated into identity_router).
Depends on: identity.dependencies (AuthService provider, current principal),
            identity.schemas.auth_schemas, identity.services.auth_service.
Key invariants:
  - No business logic here — each handler is a thin pass-through to AuthService.
  - /auth/me reads the verified Principal (aud='company'); a missing/invalid token
    yields 401 via the dependency, never 403.
  - login/refresh run on a plain session (no token yet) provided by get_auth_service.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.identity.dependencies import get_auth_service, get_current_principal
from app.identity.principal import Principal
from app.identity.schemas.auth_schemas import (
    AuthenticatedUserResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    TokenPairResponse,
)
from app.identity.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenPairResponse)
async def login(
    payload: LoginRequest, service: AuthService = Depends(get_auth_service)
) -> TokenPairResponse:
    """Authenticate a company user; return access+refresh tokens and the user view."""
    return await service.login(payload.email, payload.password)


@router.post("/refresh", response_model=TokenPairResponse, response_model_exclude_none=True)
async def refresh(
    payload: RefreshRequest, service: AuthService = Depends(get_auth_service)
) -> TokenPairResponse:
    """Rotate a refresh token; return a brand-new access+refresh pair.

    Excludes the null `user` field so the body is exactly {access_token,
    refresh_token, token_type} per SPEC §4 (user is returned only on /auth/login).
    """
    return await service.refresh(payload.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: LogoutRequest, service: AuthService = Depends(get_auth_service)
) -> None:
    """Revoke the presented refresh token (idempotent)."""
    await service.logout(payload.refresh_token)


@router.get("/me", response_model=AuthenticatedUserResponse)
async def read_current_user(
    principal: Principal = Depends(get_current_principal),
    service: AuthService = Depends(get_auth_service),
) -> AuthenticatedUserResponse:
    """Return the authenticated company user resolved from the verified token."""
    return await service.build_authenticated_user_by_id(principal.subject_id)
