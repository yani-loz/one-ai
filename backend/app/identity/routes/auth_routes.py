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

from fastapi import APIRouter, Depends, Request, Response, status

from app.identity.dependencies import get_auth_service, get_current_principal
from app.identity.exceptions import RefreshTokenInvalidError
from app.identity.principal import Principal
from app.identity.routes.cookies import (
    clear_refresh_cookie,
    client_ip_from_request,
    read_refresh_cookie,
    require_first_party_request,
    set_refresh_cookie,
)
from app.identity.schemas.auth_schemas import (
    AuthenticatedUserResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    TokenPairResponse,
)
from app.identity.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _refresh_token_from(
    request: Request, payload: RefreshRequest | LogoutRequest | None
) -> str | None:
    """Resolve the presented refresh token: httpOnly cookie first, request body as fallback.

    Browser clients send NOTHING in the body — the token rides the httpOnly cookie (Control C),
    so this is the normal path. A non-browser API client may instead present it in the JSON body.
    The cookie wins when both are present. Returns None when neither carries a token.

    Caveat (non-browser clients only): the optional body is validated by FastAPI BEFORE the
    handler runs, so a *malformed* body (e.g. `{}` or an empty `refresh_token`) is rejected 422
    by request validation even if a valid cookie is present — i.e. cookie-first holds only when
    the accompanying body is well-formed-or-absent. Browser clients send no body, so they are
    unaffected; a non-browser client should send EITHER a valid body OR no body at all.
    """
    return read_refresh_cookie(request) or (payload.refresh_token if payload is not None else None)


@router.post("/login", response_model=TokenPairResponse, response_model_exclude_none=True)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    service: AuthService = Depends(get_auth_service),
) -> TokenPairResponse:
    """Authenticate a company user; set the httpOnly refresh cookie + return the access token.

    Passes the client IP into the service so the pre-bcrypt throttle (N-01) can key a
    per-IP budget; over-limit -> 429 before any bcrypt work. The refresh token is written to
    an httpOnly cookie (Control C) and stripped from the JSON body, so an injected script can
    never read it — the body is {access_token, token_type, user}.
    """
    pair = await service.login(payload.email, payload.password, client_ip_from_request(request))
    assert pair.refresh_token is not None  # invariant: a successful login always issues one
    set_refresh_cookie(response, pair.refresh_token)
    return pair.model_copy(update={"refresh_token": None})


@router.post(
    "/refresh",
    response_model=TokenPairResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_first_party_request)],  # CSRF: reject cross-site browser POSTs
)
async def refresh(
    request: Request,
    response: Response,
    payload: RefreshRequest | None = None,
    service: AuthService = Depends(get_auth_service),
) -> TokenPairResponse:
    """Rotate the refresh token (read from the httpOnly cookie); re-set the rotated cookie.

    The presented token comes from the httpOnly cookie (browser) or the JSON body (non-browser
    fallback); a missing token is a 401. The rotated refresh token is written back to the
    cookie and excluded from the body — which is exactly {access_token, token_type}.
    """
    token = _refresh_token_from(request, payload)
    if token is None:
        raise RefreshTokenInvalidError("No refresh token presented.")
    pair = await service.refresh(token)
    assert pair.refresh_token is not None  # invariant: a successful rotation always issues one
    set_refresh_cookie(response, pair.refresh_token)
    return pair.model_copy(update={"refresh_token": None})


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_first_party_request)],  # CSRF: reject cross-site browser POSTs
)
async def logout(
    request: Request,
    response: Response,
    payload: LogoutRequest | None = None,
    service: AuthService = Depends(get_auth_service),
) -> None:
    """Revoke the presented refresh token (cookie or body) and clear the cookie (idempotent)."""
    token = _refresh_token_from(request, payload)
    if token is not None:
        await service.logout(token)
    clear_refresh_cookie(response)


@router.get("/me", response_model=AuthenticatedUserResponse)
async def read_current_user(
    principal: Principal = Depends(get_current_principal),
    service: AuthService = Depends(get_auth_service),
) -> AuthenticatedUserResponse:
    """Return the authenticated company user resolved from the verified token."""
    return await service.build_authenticated_user_by_id(principal.subject_id)
