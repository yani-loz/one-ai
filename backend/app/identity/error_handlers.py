"""
Role: Maps identity-domain exceptions to HTTP responses. Registered once from main.py.
Used by: app.main (register_identity_exception_handlers(app)).
Depends on: app.identity.exceptions, fastapi.
Key invariants:
  - Single source of the identity exception -> status-code mapping (co-located with the
    exceptions, not scattered): 401 (InvalidCredentials/TokenInvalid/TokenExpired/
    RefreshTokenInvalid), 403 (PermissionDenied), 404 (*NotFound), 409 (Duplicate*,
    LastAdmin), 429 (RateLimited).
  - Response bodies carry only the exception's own message, which is already GENERIC
    for auth failures (no enumeration). No password/hash/token ever reaches a response.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.identity.exceptions import (
    CrossSiteRequestRejectedError,
    DuplicateOrganizationError,
    DuplicateUserError,
    ErasureConfirmationError,
    ErasureNotConfiguredError,
    IdentityError,
    InvalidCredentialsError,
    InvalidGrantTransitionError,
    LastAdminError,
    LegalHoldError,
    OrganizationNotFoundError,
    OrganizationSuspendedError,
    PasswordConfirmationError,
    PermissionDeniedError,
    RateLimitedError,
    RefreshTokenInvalidError,
    SupportGrantNotFoundError,
    TokenExpiredError,
    TokenInvalidError,
    UserNotFoundError,
)

# Each identity error type -> the HTTP status it maps to.
_STATUS_BY_EXCEPTION: dict[type[IdentityError], int] = {
    ErasureConfirmationError: status.HTTP_400_BAD_REQUEST,
    InvalidCredentialsError: status.HTTP_401_UNAUTHORIZED,
    TokenInvalidError: status.HTTP_401_UNAUTHORIZED,
    TokenExpiredError: status.HTTP_401_UNAUTHORIZED,
    RefreshTokenInvalidError: status.HTTP_401_UNAUTHORIZED,
    PermissionDeniedError: status.HTTP_403_FORBIDDEN,
    CrossSiteRequestRejectedError: status.HTTP_403_FORBIDDEN,
    OrganizationSuspendedError: status.HTTP_403_FORBIDDEN,
    PasswordConfirmationError: status.HTTP_403_FORBIDDEN,
    UserNotFoundError: status.HTTP_404_NOT_FOUND,
    OrganizationNotFoundError: status.HTTP_404_NOT_FOUND,
    SupportGrantNotFoundError: status.HTTP_404_NOT_FOUND,
    DuplicateUserError: status.HTTP_409_CONFLICT,
    DuplicateOrganizationError: status.HTTP_409_CONFLICT,
    LastAdminError: status.HTTP_409_CONFLICT,
    InvalidGrantTransitionError: status.HTTP_409_CONFLICT,
    LegalHoldError: status.HTTP_409_CONFLICT,
    RateLimitedError: status.HTTP_429_TOO_MANY_REQUESTS,
    # A wiring error (no erasure hooks registered), not a client error — fail closed loudly.
    ErasureNotConfiguredError: status.HTTP_500_INTERNAL_SERVER_ERROR,
}


def register_identity_exception_handlers(app: FastAPI) -> None:
    """Register one handler per identity exception type on `app`.

    Contract: after this runs, raising any mapped IdentityError from a route/service/
    dependency yields the corresponding status code with a JSON `{"detail": <message>}`
    body. The catch-all IdentityError handler returns 401 as a safe default for any
    future auth-related error that lacks an explicit mapping.
    """
    for exception_type, status_code in _STATUS_BY_EXCEPTION.items():
        app.add_exception_handler(exception_type, _make_handler(status_code))
    app.add_exception_handler(IdentityError, _make_handler(status.HTTP_401_UNAUTHORIZED))


def _make_handler(
    status_code: int,
) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    """Build a FastAPI exception handler that responds with `status_code`."""

    async def handler(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    return handler
