"""
Role: Identity-domain exception hierarchy. All inherit app.core.exceptions.OneAIError.
Used by: identity services/repositories/dependencies (raise); app.identity.error_handlers
         maps each to an HTTP status.
Depends on: app.core.exceptions.
Key invariants:
  - Every error here subclasses OneAIError (rule A5 — no bare Exception).
  - Auth-failure messages stay GENERIC (no user enumeration, no field-level detail,
    never echo a credential, hash, or token).
  - HTTP mapping (set in error_handlers): InvalidCredentials/TokenInvalid/
    TokenExpired/RefreshTokenInvalid -> 401; PermissionDenied -> 403;
    *NotFound -> 404; Duplicate* -> 409.
"""

from __future__ import annotations

from app.core.exceptions import OneAIError


class IdentityError(OneAIError):
    """Base for all identity-domain errors."""


# — 401 Unauthorized —
class InvalidCredentialsError(IdentityError):
    """Login failed. Message is deliberately generic to prevent user enumeration."""


class TokenInvalidError(IdentityError):
    """Access token missing, malformed, wrong-signature, or wrong-audience."""


class TokenExpiredError(IdentityError):
    """Access token signature is valid but the token has expired."""


class RefreshTokenInvalidError(IdentityError):
    """Refresh token is unknown, already revoked, or expired."""


# — 403 Forbidden —
class PermissionDeniedError(IdentityError):
    """Authenticated subject lacks the role required for this operation."""


# — 404 Not Found —
class UserNotFoundError(IdentityError):
    """No user with the given id exists within the caller's org scope."""


class OrganizationNotFoundError(IdentityError):
    """No organization with the given id/slug exists."""


# — 409 Conflict —
class DuplicateUserError(IdentityError):
    """A user with the given email already exists (email is globally unique)."""


class DuplicateOrganizationError(IdentityError):
    """An organization with the given slug already exists."""


class LastAdminError(IdentityError):
    """The change would leave the organization with no active company_admin.

    Guards against an admin locking their own org out of user management by
    demoting/deactivating the last active administrator (including themselves).
    """
