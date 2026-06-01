"""
Role: FastAPI dependency wiring for the identity module — JWT verification, role
      gates, the tenant-session seam, and service providers.
Used by: routes.auth_routes / user_routes / platform_routes.
Depends on: app.core.database (get_session, scoped_session), security.tokens,
            identity repositories/services, identity.principal, identity.exceptions,
            identity.enums.
Key invariants:
  - get_current_principal verifies signature + audience('company') + expiry, then
    builds a Principal from the claims. Wrong audience / expired / missing token ->
    401. Platform tokens (aud='platform') are rejected here, and vice versa in
    get_current_platform_admin (aud='platform').
  - HTTPBearer uses auto_error=False so a MISSING token raises TokenInvalidError (401),
    not the library's default 403.
  - get_tenant_session derives org_id ONLY from the verified principal and opens a
    tenant-scoped session via core.scoped_session — the org GUC is bound per
    transaction. org_id never comes from a header or body.
  - require_company_admin enforces role=company_admin (member -> 403).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session, scoped_session
from app.identity.enums import SubjectType, UserRole
from app.identity.exceptions import PermissionDeniedError, TokenInvalidError
from app.identity.principal import Principal
from app.identity.repositories.audit_repository import AuditRepository
from app.identity.repositories.organization_repository import OrganizationRepository
from app.identity.repositories.platform_admin_repository import PlatformAdminRepository
from app.identity.repositories.refresh_token_repository import RefreshTokenRepository
from app.identity.repositories.user_repository import UserRepository
from app.identity.security.tokens import (
    COMPANY_AUDIENCE,
    PLATFORM_AUDIENCE,
    decode_access_token,
)
from app.identity.services.audit_service import AuditService
from app.identity.services.auth_service import AuthService
from app.identity.services.platform_auth_service import PlatformAuthService
from app.identity.services.platform_org_service import PlatformOrgService
from app.identity.services.token_issuer import TokenIssuer
from app.identity.services.token_rotator import TokenRotator
from app.identity.services.user_service import UserService

# auto_error=False: a MISSING Authorization header yields None (we raise 401), instead
# of HTTPBearer's default 403 — SPEC §4 wants 401 for no/invalid token.
_bearer_scheme = HTTPBearer(auto_error=False)


def _principal_from_claims(claims: dict[str, object], subject_type: SubjectType) -> Principal:
    """Build a Principal from verified JWT claims for the given subject type.

    org_id is parsed only for users (platform admins carry org_id=None). Malformed
    claim values raise TokenInvalidError (-> 401) rather than leaking a 500.
    """
    try:
        subject_id = UUID(str(claims["sub"]))
        role = str(claims["role"])
        raw_org = claims.get("org_id")
        org_id = UUID(str(raw_org)) if raw_org else None
    except (KeyError, ValueError) as exc:
        raise TokenInvalidError("Access token claims are malformed.") from exc
    return Principal(
        subject_id=subject_id, org_id=org_id, role=role, subject_type=subject_type.value
    )


async def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> Principal:
    """Verify a COMPANY access token and return the caller's Principal.

    Contract: requires a Bearer token whose signature + aud='company' + expiry all
    verify. Returns a Principal with a non-None org_id.

    Raises:
        TokenInvalidError: missing/malformed/wrong-audience/bad-signature token (401).
        TokenExpiredError: expired token (401).
    """
    if credentials is None:
        raise TokenInvalidError("Missing bearer token.")
    claims = decode_access_token(credentials.credentials, COMPANY_AUDIENCE)
    return _principal_from_claims(claims, SubjectType.user)


async def require_company_admin(
    principal: Principal = Depends(get_current_principal),
) -> Principal:
    """Allow only company_admin callers (members -> 403).

    Raises:
        PermissionDeniedError: the caller is not a company_admin (-> 403).
    """
    if principal.role != UserRole.company_admin.value:
        raise PermissionDeniedError("Company administrator role required.")
    return principal


async def get_current_platform_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> Principal:
    """Verify a PLATFORM access token and return the platform admin's Principal.

    Contract: requires a Bearer token whose signature + aud='platform' + expiry all
    verify. A company token (aud='company') is rejected by the audience check.

    Raises:
        TokenInvalidError / TokenExpiredError: missing/invalid/expired/wrong-aud (401).
    """
    if credentials is None:
        raise TokenInvalidError("Missing bearer token.")
    claims = decode_access_token(credentials.credentials, PLATFORM_AUDIENCE)
    return _principal_from_claims(claims, SubjectType.platform_admin)


async def get_tenant_session(
    principal: Principal = Depends(get_current_principal),
) -> AsyncIterator[AsyncSession]:
    """Yield a tenant-scoped session bound to the caller's verified org_id.

    The org_id comes ONLY from the verified JWT (principal.org_id) — never a header or
    body. The session's Postgres GUC is bound per transaction (see core.scoped_session),
    the seam RLS policies bind to (policies are DEFINED in migration 0003; DB-level
    enforcement is deferred until the app connects as a least-privilege role — see
    docs/FIX_BEFORE_PROD.md). This dependency is the unit-of-work boundary: it commits
    on success and rolls back on any exception, so services stay transaction-free.
    """
    if principal.org_id is None:
        raise TokenInvalidError("Access token is missing an organization scope.")
    async with scoped_session(principal.org_id) as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


# — Service providers (constructor injection over FastAPI Depends) —


def get_auth_service(session: AsyncSession = Depends(get_session)) -> AuthService:
    """Provide AuthService on a PLAIN session (login/refresh have no tenant yet).

    The AuditService shares this session, so a success event commits atomically with the
    login/refresh; failure/blocked events use the AuditService's own independent session.
    """
    refresh_tokens = RefreshTokenRepository(session)
    return AuthService(
        users=UserRepository(session),
        organizations=OrganizationRepository(session),
        token_issuer=TokenIssuer(refresh_tokens),
        token_rotator=TokenRotator(refresh_tokens),
        audit=AuditService(AuditRepository(session)),
    )


def get_user_service(session: AsyncSession = Depends(get_tenant_session)) -> UserService:
    """Provide UserService on the caller's TENANT-scoped session (admin CRUD)."""
    return UserService(users=UserRepository(session))


def get_platform_auth_service(
    session: AsyncSession = Depends(get_session),
) -> PlatformAuthService:
    """Provide PlatformAuthService on a PLAIN session (platform spans all orgs)."""
    refresh_tokens = RefreshTokenRepository(session)
    return PlatformAuthService(
        platform_admins=PlatformAdminRepository(session),
        organizations=OrganizationRepository(session),
        users=UserRepository(session),
        token_issuer=TokenIssuer(refresh_tokens),
        token_rotator=TokenRotator(refresh_tokens),
        audit=AuditService(AuditRepository(session)),
    )


def get_platform_org_service(
    session: AsyncSession = Depends(get_session),
) -> PlatformOrgService:
    """Provide PlatformOrgService on a PLAIN session (org lifecycle spans all orgs).

    The AuditService shares this session, so each lifecycle event (suspend/reactivate/
    legal-hold) commits atomically with the status/legal-hold change.
    """
    return PlatformOrgService(
        organizations=OrganizationRepository(session),
        audit=AuditService(AuditRepository(session)),
    )


def get_audit_service(session: AsyncSession = Depends(get_session)) -> AuditService:
    """Provide AuditService on a PLAIN session for the audit READ endpoints (spans orgs)."""
    return AuditService(AuditRepository(session))
