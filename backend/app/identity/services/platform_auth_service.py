"""
Role: Platform-admin authentication + organization onboarding/listing. The SEPARATE
      auth domain (own login, own token audience). Holds platform business logic (A5).
Used by: routes.platform_routes; constructed in identity.dependencies on a PLAIN
         (non-tenant) session — the platform spans all orgs.
Depends on: platform-admin/organization/user/refresh-token repositories,
            security.password, security.tokens, token_issuer + token_rotator,
            identity.schemas, identity.principal, identity.exceptions, identity.enums.
Key invariants:
  - Platform login verifies the bcrypt password + active flag; failure -> generic
    InvalidCredentialsError (no enumeration). Tokens carry aud='platform' /
    subject_type='platform_admin' — rejected on company endpoints by audience.
  - A platform admin's Principal has org_id=None (global scope, not an org row).
  - onboard_organization creates the org + its first company_admin ATOMICALLY in one
    session/transaction; a duplicate slug or admin email aborts before any write
    commits (caller commits once on success).
  - list_organizations returns METADATA ONLY (incl. user_count) — never tenant content.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from app.identity.enums import UserRole
from app.identity.exceptions import (
    DuplicateOrganizationError,
    DuplicateUserError,
    InvalidCredentialsError,
)
from app.identity.models.organization import Organization
from app.identity.models.user import User
from app.identity.principal import Principal
from app.identity.repositories.organization_repository import OrganizationRepository
from app.identity.repositories.platform_admin_repository import PlatformAdminRepository
from app.identity.repositories.user_repository import UserRepository
from app.identity.schemas.platform_schemas import (
    OrganizationCreateRequest,
    OrganizationOnboardedResponse,
    OrganizationResponse,
)
from app.identity.schemas.user_schemas import UserResponse
from app.identity.security.password import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    verify_password,
)
from app.identity.security.tokens import PLATFORM_AUDIENCE
from app.identity.services.token_issuer import TokenIssuer
from app.identity.services.token_rotator import TokenRotator

_PLATFORM_SUBJECT_TYPE = "platform_admin"


class PlatformAuthService:
    """Platform-domain authentication and organization administration."""

    def __init__(
        self,
        platform_admins: PlatformAdminRepository,
        organizations: OrganizationRepository,
        users: UserRepository,
        token_issuer: TokenIssuer,
        token_rotator: TokenRotator,
    ) -> None:
        """Wire the repositories + token helpers (all bound to one plain session)."""
        self._platform_admins = platform_admins
        self._organizations = organizations
        self._users = users
        self._token_issuer = token_issuer
        self._token_rotator = token_rotator

    async def login(self, email: str, password: str) -> tuple[str, str]:
        """Authenticate a platform admin; return a fresh (access, refresh) pair.

        Raises:
            InvalidCredentialsError: unknown email, wrong password, or inactive
                account — one generic error to prevent enumeration.
        """
        admin = await self._platform_admins.get_by_email(email)
        # Always run bcrypt (real hash or dummy) so an unknown/inactive admin email is
        # indistinguishable from a real one by response time (no enumeration oracle).
        password_hash = admin.password_hash if admin is not None else DUMMY_PASSWORD_HASH
        password_ok = verify_password(password, password_hash)
        if admin is None or not admin.is_active or not password_ok:
            raise InvalidCredentialsError("Invalid email or password.")

        principal = Principal(
            subject_id=admin.id,
            org_id=None,
            role=_PLATFORM_SUBJECT_TYPE,
            subject_type=_PLATFORM_SUBJECT_TYPE,
        )
        return await self._token_issuer.issue_pair(
            principal, PLATFORM_AUDIENCE, _PLATFORM_SUBJECT_TYPE
        )

    async def refresh(self, raw_refresh_token: str) -> tuple[str, str]:
        """Rotate a platform refresh token: revoke the old, issue a new pair.

        Raises:
            RefreshTokenInvalidError: token unknown/revoked/expired (-> 401).
            InvalidCredentialsError: the admin vanished or was deactivated.
        """
        consumed = await self._token_rotator.consume(raw_refresh_token, _PLATFORM_SUBJECT_TYPE)
        admin = await self._platform_admins.get_by_id(consumed.subject_id)
        if admin is None or not admin.is_active:
            raise InvalidCredentialsError("Invalid email or password.")

        principal = Principal(
            subject_id=admin.id,
            org_id=None,
            role=_PLATFORM_SUBJECT_TYPE,
            subject_type=_PLATFORM_SUBJECT_TYPE,
        )
        return await self._token_issuer.issue_pair(
            principal, PLATFORM_AUDIENCE, _PLATFORM_SUBJECT_TYPE
        )

    async def logout(self, raw_refresh_token: str) -> None:
        """Revoke the presented platform refresh token (idempotent)."""
        await self._token_rotator.revoke(raw_refresh_token)

    async def onboard_organization(
        self, payload: OrganizationCreateRequest
    ) -> OrganizationOnboardedResponse:
        """Create a new org and its first company_admin atomically.

        Contract: validates slug + admin-email uniqueness, inserts the org, then the
        admin user (role=company_admin) scoped to that org with a bcrypt-hashed
        password. Returns both as metadata/views. Caller commits once.

        Raises:
            DuplicateOrganizationError: the slug is already taken (-> 409).
            DuplicateUserError: the admin email already belongs to a user (-> 409).
        """
        if await self._organizations.get_by_slug(payload.org_slug) is not None:
            raise DuplicateOrganizationError("An organization with this slug already exists.")
        if await self._users.email_exists(payload.admin_email):
            raise DuplicateUserError("A user with this email already exists.")

        try:
            organization = await self._organizations.add(
                Organization(name=payload.org_name, slug=payload.org_slug)
            )
        except IntegrityError as exc:
            # Lost a concurrent race against organizations.slug UNIQUE (AUD-05) -> 409.
            raise DuplicateOrganizationError(
                "An organization with this slug already exists."
            ) from exc
        try:
            admin = await self._users.add(
                User(
                    org_id=organization.id,
                    email=payload.admin_email,
                    full_name=payload.admin_full_name,
                    password_hash=hash_password(payload.admin_password),
                    role=UserRole.company_admin.value,
                )
            )
        except IntegrityError as exc:
            # Lost a concurrent race against users.email UNIQUE (AUD-05) -> 409; the whole
            # onboarding transaction rolls back, so no orphan org is committed.
            raise DuplicateUserError("A user with this email already exists.") from exc
        return OrganizationOnboardedResponse(
            organization=OrganizationResponse(
                id=organization.id,
                name=organization.name,
                slug=organization.slug,
                status=organization.status,
                user_count=1,
                created_at=organization.created_at,
            ),
            admin=UserResponse.model_validate(admin),
        )

    async def list_organizations(self) -> list[OrganizationResponse]:
        """Return all organizations as metadata (id/name/slug/status/user_count/date)."""
        rows = await self._organizations.list_all_with_user_counts()
        return [
            OrganizationResponse(
                id=org.id,
                name=org.name,
                slug=org.slug,
                status=org.status,
                user_count=user_count,
                created_at=org.created_at,
            )
            for org, user_count in rows
        ]
