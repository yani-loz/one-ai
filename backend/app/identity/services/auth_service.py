"""
Role: Company-user authentication — login, refresh (rotation), logout, and building
      the authenticated-user view. Holds the auth business logic (rule A5).
Used by: routes.auth_routes; constructed in identity.dependencies.
Depends on: user/organization/refresh-token repositories, security.password,
            security.tokens, services.token_issuer + token_rotator, identity.schemas,
            identity.principal, identity.exceptions.
Key invariants:
  - Login verifies the bcrypt password and that the account is active; any failure
    raises InvalidCredentialsError with a GENERIC message (no user enumeration, no
    "wrong password" vs "no such user" distinction).
  - Tokens carry aud='company' / subject_type='user'. Refresh is single-use rotation.
  - Runs on a PLAIN session (login has no token yet, so no tenant scope) — email is
    globally unique and the password gates access.
"""

from __future__ import annotations

from uuid import UUID

from app.identity.enums import AuditActorType, OrganizationStatus
from app.identity.exceptions import (
    InvalidCredentialsError,
    OrganizationNotFoundError,
    OrganizationSuspendedError,
)
from app.identity.models.organization import Organization
from app.identity.models.user import User
from app.identity.principal import Principal
from app.identity.repositories.organization_repository import OrganizationRepository
from app.identity.repositories.user_repository import UserRepository
from app.identity.schemas.auth_schemas import AuthenticatedUserResponse, TokenPairResponse
from app.identity.security.password import DUMMY_PASSWORD_HASH, verify_password
from app.identity.security.tokens import COMPANY_AUDIENCE
from app.identity.services.audit_service import AuditAction, AuditEvent, AuditService
from app.identity.services.token_issuer import TokenIssuer
from app.identity.services.token_rotator import TokenRotator

_USER_SUBJECT_TYPE = "user"


class AuthService:
    """Company-domain authentication and session lifecycle."""

    def __init__(
        self,
        users: UserRepository,
        organizations: OrganizationRepository,
        token_issuer: TokenIssuer,
        token_rotator: TokenRotator,
        audit: AuditService,
    ) -> None:
        """Wire the repositories, token helpers, and audit writer (one plain session)."""
        self._users = users
        self._organizations = organizations
        self._token_issuer = token_issuer
        self._token_rotator = token_rotator
        self._audit = audit

    async def login(self, email: str, password: str) -> TokenPairResponse:
        """Authenticate a company user and issue a fresh token pair.

        Contract: verifies the account exists, is active, and the bcrypt password
        matches, then returns access+refresh tokens plus the authenticated-user view.

        Raises:
            InvalidCredentialsError: any failure (unknown email, wrong password,
                inactive account) — one generic error to prevent enumeration.
        """
        user = await self._users.get_by_email(email)
        # Always run bcrypt — against the user's hash, or a dummy when no account
        # matched — so unknown/inactive accounts cost the same as a real login and
        # cannot be enumerated by response time.
        password_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
        password_ok = verify_password(password, password_hash)
        if user is None or not user.is_active or not password_ok:
            # Failed login is recorded on an INDEPENDENT session: this path raises, so a
            # same-session row would be rolled back. actor_id is null; actor_email is the
            # attempted (already-normalized) email — internal log only, never surfaced to
            # the generic 401 response, so it adds no enumeration oracle.
            await self._audit.record_independently(
                AuditEvent(
                    action=AuditAction.LOGIN_FAILURE,
                    actor_type=AuditActorType.user,
                    actor_email=email,
                    details={"reason": "invalid_credentials"},
                )
            )
            raise InvalidCredentialsError("Invalid email or password.")

        # Credentials are valid; now gate on the org's lifecycle. A suspended org blocks
        # login (403) — raised only AFTER the password check, so it is not an enumeration
        # oracle (an attacker without valid creds can't distinguish a suspended org).
        try:
            organization = await self._load_loginable_org(user.org_id)
        except OrganizationSuspendedError:
            # Also a rolling-back path -> independent writer (valuable for incident response:
            # someone with valid creds trying to use a suspended org).
            await self._audit.record_independently(
                AuditEvent(
                    action=AuditAction.LOGIN_BLOCKED,
                    actor_type=AuditActorType.user,
                    actor_id=user.id,
                    actor_email=user.email,
                    org_id=user.org_id,
                    details={"reason": "org_suspended"},
                )
            )
            raise
        principal = self._principal_for(user)
        access_token, refresh_token = await self._token_issuer.issue_pair(
            principal, COMPANY_AUDIENCE, _USER_SUBJECT_TYPE
        )
        # Success is recorded on the REQUEST session: it commits atomically with the
        # issued refresh token, so a successful login can never be silently unlogged.
        await self._audit.record(
            AuditEvent(
                action=AuditAction.LOGIN_SUCCESS,
                actor_type=AuditActorType.user,
                actor_id=user.id,
                actor_email=user.email,
                org_id=user.org_id,
            )
        )
        user_view = self._build_user_view(user, organization)
        return TokenPairResponse(
            access_token=access_token, refresh_token=refresh_token, user=user_view
        )

    async def refresh(self, raw_refresh_token: str) -> TokenPairResponse:
        """Rotate a refresh token: revoke the old one, issue a brand-new pair.

        Raises:
            RefreshTokenInvalidError: token unknown/revoked/expired (-> 401).
            InvalidCredentialsError: the token's user vanished or was deactivated.
        """
        consumed = await self._token_rotator.consume(raw_refresh_token, _USER_SUBJECT_TYPE)
        user = await self._users.get_by_subject_id(consumed.subject_id)
        if user is None or not user.is_active:
            raise InvalidCredentialsError("Invalid email or password.")

        # A suspended org cannot extend its session either — block refresh (403), so a
        # long-lived refresh token can't outlive a suspension.
        await self._load_loginable_org(user.org_id)
        principal = self._principal_for(user)
        access_token, refresh_token = await self._token_issuer.issue_pair(
            principal, COMPANY_AUDIENCE, _USER_SUBJECT_TYPE
        )
        # Same-session: the rotation (old revoked + new issued) and its audit row commit
        # together.
        await self._audit.record(
            AuditEvent(
                action=AuditAction.REFRESH,
                actor_type=AuditActorType.user,
                actor_id=user.id,
                actor_email=user.email,
                org_id=user.org_id,
            )
        )
        return TokenPairResponse(access_token=access_token, refresh_token=refresh_token)

    async def logout(self, raw_refresh_token: str) -> None:
        """Revoke the presented refresh token (idempotent — unknown token is a no-op)."""
        await self._token_rotator.revoke(raw_refresh_token)
        # The opaque token is not resolved to a subject here, so the actor is unattributed
        # (enriching it is a tracked follow-up); the event + IP are still recorded. Same
        # session — logout returns 204 and commits.
        await self._audit.record(
            AuditEvent(action=AuditAction.LOGOUT, actor_type=AuditActorType.user)
        )

    async def build_authenticated_user_by_id(
        self, user_id: UUID
    ) -> AuthenticatedUserResponse:
        """Build the /auth/me view for the verified subject id from the access token.

        Contract: `user_id` originates from a signature+audience+expiry-verified JWT,
        so resolving the user by id (without an org filter) is safe here.

        Raises:
            InvalidCredentialsError: the user no longer exists or was deactivated
                (token outlived the account) — mapped to 401.
        """
        user = await self._users.get_by_subject_id(user_id)
        if user is None or not user.is_active:
            raise InvalidCredentialsError("Invalid email or password.")
        organization = await self._organizations.get_by_id(user.org_id)
        if organization is None:
            raise OrganizationNotFoundError("Organization for user not found.")
        return self._build_user_view(user, organization)

    @staticmethod
    def _principal_for(user: User) -> Principal:
        """Construct the JWT Principal for an org-scoped user."""
        return Principal(
            subject_id=user.id,
            org_id=user.org_id,
            role=user.role,
            subject_type=_USER_SUBJECT_TYPE,
        )

    async def _load_loginable_org(self, org_id: UUID) -> Organization:
        """Resolve a user's org for login/refresh, rejecting a SUSPENDED one.

        ONLY `suspended` blocks here — `onboarding` and `offboarded` orgs are allowed to
        authenticate (offboarded access-cutoff belongs to PC-06's erasure scope, not this
        gate). The method is named for what it guarantees: the org can log in, not that it
        is strictly 'active'.

        Raises:
            OrganizationNotFoundError: the org row is missing (-> 404).
            OrganizationSuspendedError: the org is suspended (-> 403). Raised only after
                credentials verify, so it is never a user-enumeration oracle.
        """
        organization = await self._organizations.get_by_id(org_id)
        if organization is None:
            raise OrganizationNotFoundError("Organization for user not found.")
        if organization.status == OrganizationStatus.suspended.value:
            raise OrganizationSuspendedError("Your organization's access is suspended.")
        return organization

    @staticmethod
    def _build_user_view(
        user: User, organization: Organization
    ) -> AuthenticatedUserResponse:
        """Assemble AuthenticatedUserResponse from the user + its (already-loaded) org."""
        return AuthenticatedUserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            org_id=user.org_id,
            org_name=organization.name,
        )
