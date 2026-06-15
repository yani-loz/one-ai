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
    *NotFound -> 404; Duplicate* -> 409; RateLimited -> 429.
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


class CrossSiteRequestRejectedError(IdentityError):
    """A cross-site browser request hit a cookie-authenticated state-changing endpoint.

    CSRF defense for the httpOnly-cookie auth endpoints (/auth/refresh, /auth/logout): when the
    refresh cookie is SameSite=None (the documented cross-origin posture), SameSite no longer
    blocks forgery, so these endpoints additionally require a first-party Sec-Fetch-Site or an
    allowlisted Origin. Maps to 403.
    """


class OrganizationSuspendedError(IdentityError):
    """The user's organization is suspended — login and refresh are blocked (-> 403).

    Raised only AFTER the password verifies, so it is never a user-enumeration oracle:
    an attacker without valid credentials can't distinguish a suspended org from any
    other failed login.
    """


class PasswordConfirmationError(IdentityError):
    """Re-authentication for a sensitive action failed — wrong password (-> 403).

    Used to gate destructive operations (e.g. erasure) behind a fresh password check, even
    for an already-authenticated admin (sudo-style). Mapped to 403 (NOT 401) so a wrong
    password never tears down the valid session — the admin just retries.
    """


# — 429 Too Many Requests —
class RateLimitedError(IdentityError):
    """Login attempts from this IP / for this account exceeded the throttle (-> 429).

    Raised by the pre-bcrypt throttle (security/rate_limit.py) BEFORE any bcrypt work, so a
    credential-stuffing / bcrypt-CPU-DoS attacker is blocked at near-zero server cost. The
    message carries only a retry hint — never which budget (IP vs account) tripped, never a
    credential — so it adds no enumeration oracle.
    """


# — 404 Not Found —
class UserNotFoundError(IdentityError):
    """No user with the given id exists within the caller's org scope."""


class OrganizationNotFoundError(IdentityError):
    """No organization with the given id/slug exists."""


class SupportGrantNotFoundError(IdentityError):
    """No support grant with the given id is visible in the caller's scope (-> 404).

    A company admin loading another org's grant, or a platform admin loading a grant they
    did not request, resolves here — never a cross-org existence leak (a 404, not a 403).
    """


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


class InvalidGrantTransitionError(IdentityError):
    """The requested support-grant transition is illegal from its current state (-> 409).

    e.g. approving/denying a grant that is not `requested`, or revoking one that is already
    `denied`/`revoked`. Every transition is guarded on the grant's CURRENT status so a
    revoked grant can't be re-approved and an expired one can't be resurrected.
    """


class LegalHoldError(IdentityError):
    """Erasure was refused because the organization is under legal hold (-> 409).

    Legal-hold-beats-erasure: a hold (e.g. litigation / regulatory preservation) overrides
    the right to erasure, so nothing is deleted until the hold is cleared.
    """


# — 400 Bad Request —
class ErasureConfirmationError(IdentityError):
    """The erasure confirmation did not match the organization (-> 400).

    A guard against accidental destruction: the request must echo the org's slug exactly
    (GitHub-style "type the name to delete"). A mismatch deletes nothing.
    """


# — 500 Internal Server Error —
class ErasureNotConfiguredError(IdentityError):
    """Erasure was refused because NO feature-module erasure hooks are registered (-> 500).

    Fail-closed guard: an empty hook registry means Connect tables + the entity graph would
    silently survive while a deletion certificate is still issued (the exact partial-erasure
    bug the hook registry exists to prevent). Triggers only in a process that constructs
    ErasureService without running app.main.create_app() (which registers the hooks) — a
    deployment/wiring error, so 500, and NOTHING is deleted.
    """
