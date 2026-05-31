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

from app.identity.exceptions import InvalidCredentialsError, OrganizationNotFoundError
from app.identity.models.user import User
from app.identity.principal import Principal
from app.identity.repositories.organization_repository import OrganizationRepository
from app.identity.repositories.user_repository import UserRepository
from app.identity.schemas.auth_schemas import AuthenticatedUserResponse, TokenPairResponse
from app.identity.security.password import DUMMY_PASSWORD_HASH, verify_password
from app.identity.security.tokens import COMPANY_AUDIENCE
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
    ) -> None:
        """Wire the repositories and token helpers (all bound to one plain session)."""
        self._users = users
        self._organizations = organizations
        self._token_issuer = token_issuer
        self._token_rotator = token_rotator

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
            raise InvalidCredentialsError("Invalid email or password.")

        principal = self._principal_for(user)
        access_token, refresh_token = await self._token_issuer.issue_pair(
            principal, COMPANY_AUDIENCE, _USER_SUBJECT_TYPE
        )
        user_view = await self._build_user_view(user)
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

        principal = self._principal_for(user)
        access_token, refresh_token = await self._token_issuer.issue_pair(
            principal, COMPANY_AUDIENCE, _USER_SUBJECT_TYPE
        )
        return TokenPairResponse(access_token=access_token, refresh_token=refresh_token)

    async def logout(self, raw_refresh_token: str) -> None:
        """Revoke the presented refresh token (idempotent — unknown token is a no-op)."""
        await self._token_rotator.revoke(raw_refresh_token)

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
        return await self._build_user_view(user)

    @staticmethod
    def _principal_for(user: User) -> Principal:
        """Construct the JWT Principal for an org-scoped user."""
        return Principal(
            subject_id=user.id,
            org_id=user.org_id,
            role=user.role,
            subject_type=_USER_SUBJECT_TYPE,
        )

    async def _build_user_view(self, user: User) -> AuthenticatedUserResponse:
        """Assemble AuthenticatedUserResponse, resolving the org name for display."""
        organization = await self._organizations.get_by_id(user.org_id)
        if organization is None:
            raise OrganizationNotFoundError("Organization for user not found.")
        return AuthenticatedUserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            org_id=user.org_id,
            org_name=organization.name,
        )
