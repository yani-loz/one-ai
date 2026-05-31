"""
Role: Maps identity-domain exceptions to HTTP responses. Registered once from main.py.
Used by: app.main (register_identity_exception_handlers(app)).
Depends on: app.identity.exceptions, fastapi.
Key invariants:
  - Single source of the identity exception -> status-code mapping (co-located with the
    exceptions, not scattered): 401 (InvalidCredentials/TokenInvalid/TokenExpired/
    RefreshTokenInvalid), 403 (PermissionDenied), 404 (*NotFound), 409 (Duplicate*,
    LastAdmin).
  - Response bodies carry only the exception's own message, which is already GENERIC
    for auth failures (no enumeration). No password/hash/token ever reaches a response.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.identity.exceptions import (
    DuplicateOrganizationError,
    DuplicateUserError,
    IdentityError,
    InvalidCredentialsError,
    LastAdminError,
    OrganizationNotFoundError,
    PermissionDeniedError,
    RefreshTokenInvalidError,
    TokenExpiredError,
    TokenInvalidError,
    UserNotFoundError,
)

# Each identity error type -> the HTTP status it maps to.
_STATUS_BY_EXCEPTION: dict[type[IdentityError], int] = {
    InvalidCredentialsError: status.HTTP_401_UNAUTHORIZED,
    TokenInvalidError: status.HTTP_401_UNAUTHORIZED,
    TokenExpiredError: status.HTTP_401_UNAUTHORIZED,
    RefreshTokenInvalidError: status.HTTP_401_UNAUTHORIZED,
    PermissionDeniedError: status.HTTP_403_FORBIDDEN,
    UserNotFoundError: status.HTTP_404_NOT_FOUND,
    OrganizationNotFoundError: status.HTTP_404_NOT_FOUND,
    DuplicateUserError: status.HTTP_409_CONFLICT,
    DuplicateOrganizationError: status.HTTP_409_CONFLICT,
    LastAdminError: status.HTTP_409_CONFLICT,
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
