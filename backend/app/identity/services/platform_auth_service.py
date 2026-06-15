"""
Role: Platform-admin authentication + organization onboarding/listing. The SEPARATE
      auth domain (own login, own token audience). Holds platform business logic (A5).
Used by: routes.platform_routes; constructed in identity.dependencies on a PLAIN
         (non-tenant) session — the platform spans all orgs.
Depends on: platform-admin/organization/user/refresh-token repositories,
            security.password, security.tokens, token_issuer + token_rotator,
            services.audit_service, identity.schemas, identity.principal,
            identity.exceptions, identity.enums.
Key invariants:
  - Platform login is THROTTLED BEFORE bcrypt (FIX_BEFORE_PROD N-01), exactly like the
    company login: a per-IP + per-account sliding-window check at the top of login() blocks
    credential stuffing / a bcrypt-CPU-DoS at near-zero cost (RateLimitedError -> 429) before
    verify_password_async runs. Only a verified failure consumes the budget; DUMMY_PASSWORD_HASH
    is untouched (the throttle gates in front of the enumeration defense, not instead of it).
  - Platform login verifies the bcrypt password + active flag; failure -> generic
    InvalidCredentialsError (no enumeration). Tokens carry aud='platform' /
    subject_type='platform_admin' — rejected on company endpoints by audience.
    bcrypt (verify + hash) runs OFF the event loop via the async password helpers.
  - AUDITED like the company domain (PC-04a): login success / refresh / logout are
    recorded same-transaction (actor_type='platform_admin'); a FAILED login is recorded
    on an independent committed session so the row survives the request rollback. Who
    enters the platform console is never unlogged.
  - A platform admin's Principal has org_id=None (global scope, not an org row).
  - onboard_organization creates the org + its first company_admin ATOMICALLY in one
    session/transaction; a duplicate slug or admin email aborts before any write
    commits (caller commits once on success).
  - list_organizations returns METADATA ONLY (incl. user_count) — never tenant content.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.identity.enums import AuditActorType, UserRole
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
    PlatformAdminResponse,
)
from app.identity.schemas.user_schemas import UserResponse
from app.identity.security.password import (
    DUMMY_PASSWORD_HASH,
    hash_password_async,
    verify_password_async,
)
from app.identity.security.rate_limit import RateLimiter, account_key, ip_key
from app.identity.security.token_denylist import TokenDenylist
from app.identity.security.tokens import PLATFORM_AUDIENCE
from app.identity.services.audit_service import AuditAction, AuditEvent, AuditService
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
        audit: AuditService,
        rate_limiter: RateLimiter,
        token_denylist: TokenDenylist,
    ) -> None:
        """Wire the repositories, token helpers, audit writer, login throttle, and denylist.

        rate_limiter is the process-wide singleton (same instance the company login uses),
        so a per-IP budget spans both login surfaces — an attacker can't dodge the IP cap by
        alternating between /auth/login and /platform/login. token_denylist (also shared) lets
        platform logout immediately revoke a god-mode access token, not wait out its TTL.
        """
        self._platform_admins = platform_admins
        self._organizations = organizations
        self._users = users
        self._token_issuer = token_issuer
        self._token_rotator = token_rotator
        self._audit = audit
        self._rate_limiter = rate_limiter
        self._token_denylist = token_denylist

    async def login(
        self, email: str, password: str, client_ip: str | None = None
    ) -> tuple[str, str]:
        """Authenticate a platform admin; return a fresh (access, refresh) pair.

        Contract: THROTTLES on `client_ip` + `email` BEFORE any bcrypt work, then mirrors the
        company audit wiring (PC-04a) — success is recorded same-transaction (with the issued
        refresh token); a failure is recorded on an independent committed session before the
        generic error is raised.

        Raises:
            RateLimitedError: too many recent attempts from this IP or for this account
                (-> 429) — raised before bcrypt.
            InvalidCredentialsError: unknown email, wrong password, or inactive
                account — one generic error to prevent enumeration.
        """
        # Throttle FIRST — before the DB lookup and the bcrypt verify (same model as the
        # company login). The platform login is the highest-value target, so the throttle
        # matters most here.
        ip_throttle_key = ip_key(client_ip)
        account_throttle_key = account_key(email)
        self._rate_limiter.check(ip_throttle_key)
        self._rate_limiter.check(account_throttle_key)

        admin = await self._platform_admins.get_by_email(email)
        # Always run bcrypt (real hash or dummy) so an unknown/inactive admin email is
        # indistinguishable from a real one by response time (no enumeration oracle).
        # Offloaded to a worker thread (verify_password_async); the dummy path pays the
        # SAME awaited bcrypt cost, so the timing equalizer survives the offload.
        password_hash = admin.password_hash if admin is not None else DUMMY_PASSWORD_HASH
        password_ok = await verify_password_async(password, password_hash)
        if admin is None or not admin.is_active or not password_ok:
            # Verified failure -> consume both budgets so the next attempt backs off (429).
            # Never registered on success or a 429 — mirrors AuthService.login.
            self._rate_limiter.register_failure(ip_throttle_key)
            self._rate_limiter.register_failure(account_throttle_key)
            # Failed platform login is recorded on an INDEPENDENT session (this path
            # raises, so a same-session row would roll back). actor_email is the attempted
            # email — internal log only, never surfaced to the generic 401, so it adds no
            # enumeration oracle. Mirrors AuthService.login exactly.
            await self._audit.record_independently(
                AuditEvent(
                    action=AuditAction.LOGIN_FAILURE,
                    actor_type=AuditActorType.platform_admin,
                    actor_email=email,
                    details={"reason": "invalid_credentials"},
                )
            )
            raise InvalidCredentialsError("Invalid email or password.")

        principal = Principal(
            subject_id=admin.id,
            org_id=None,
            role=_PLATFORM_SUBJECT_TYPE,
            subject_type=_PLATFORM_SUBJECT_TYPE,
        )
        access_token, refresh_token = await self._token_issuer.issue_pair(
            principal, PLATFORM_AUDIENCE, _PLATFORM_SUBJECT_TYPE
        )
        # Success rides the REQUEST session: it commits atomically with the issued refresh
        # token, so who entered the platform console can never be silently unlogged.
        await self._audit.record(
            AuditEvent(
                action=AuditAction.LOGIN_SUCCESS,
                actor_type=AuditActorType.platform_admin,
                actor_id=admin.id,
                actor_email=admin.email,
            )
        )
        return access_token, refresh_token

    async def refresh(self, raw_refresh_token: str) -> tuple[str, str]:
        """Rotate a platform refresh token: revoke the old, issue a new pair.

        The rotation and its `auth.refresh` audit row commit together (same session),
        mirroring the company pattern.

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
        access_token, refresh_token = await self._token_issuer.issue_pair(
            principal, PLATFORM_AUDIENCE, _PLATFORM_SUBJECT_TYPE
        )
        # Same-session: the rotation (old revoked + new issued) and its audit row commit
        # together — mirrors AuthService.refresh.
        await self._audit.record(
            AuditEvent(
                action=AuditAction.REFRESH,
                actor_type=AuditActorType.platform_admin,
                actor_id=admin.id,
                actor_email=admin.email,
            )
        )
        return access_token, refresh_token

    async def logout(self, raw_refresh_token: str) -> None:
        """Revoke the presented platform refresh token AND the admin's access tokens.

        Revoking the refresh token stops new access tokens; revoking the admin subject in the
        denylist kills the god-mode access token still in flight. Idempotent (unknown -> None).
        """
        subject_id = await self._token_rotator.revoke(raw_refresh_token)
        if subject_id is not None:
            self._token_denylist.revoke_subject(subject_id)
        # Mirrors AuthService.logout: the opaque token is not resolved to a subject here,
        # so the actor is unattributed (PC-04a item (c) tracks enriching it); the event +
        # IP are still recorded. Same session — logout returns 204 and commits.
        await self._audit.record(
            AuditEvent(action=AuditAction.LOGOUT, actor_type=AuditActorType.platform_admin)
        )

    async def build_admin_view_by_id(self, admin_id: UUID) -> PlatformAdminResponse:
        """Build the /platform/me view for the verified admin id from the access token.

        Contract: `admin_id` comes from a signature+audience('platform')+expiry-verified
        JWT, so resolving by id (no org filter — platform admins are global) is safe.

        Raises:
            InvalidCredentialsError: the admin no longer exists or was deactivated (the
                token outlived the account) — mapped to 401.
        """
        admin = await self._platform_admins.get_by_id(admin_id)
        if admin is None or not admin.is_active:
            raise InvalidCredentialsError("Invalid email or password.")
        return PlatformAdminResponse.model_validate(admin)

    async def onboard_organization(
        self, payload: OrganizationCreateRequest, actor: Principal
    ) -> OrganizationOnboardedResponse:
        """Create a new org and its first company_admin atomically.

        Contract: validates slug + admin-email uniqueness, inserts the org, then the
        admin user (role=company_admin) scoped to that org with a bcrypt-hashed
        password, and records an `org.onboard` audit row on the SAME session. Returns
        both as metadata/views. Caller commits once (org + admin + audit row together).

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
        # Hash on a worker thread (hash_password_async) — bcrypt's ~300ms of CPU must not
        # stall the event loop for every other tenant while an org is onboarded.
        admin_password_hash = await hash_password_async(payload.admin_password)
        try:
            admin = await self._users.add(
                User(
                    org_id=organization.id,
                    email=payload.admin_email,
                    full_name=payload.admin_full_name,
                    password_hash=admin_password_hash,
                    role=UserRole.company_admin.value,
                )
            )
        except IntegrityError as exc:
            # Lost a concurrent race against users.email UNIQUE (AUD-05) -> 409; the whole
            # onboarding transaction rolls back, so no orphan org is committed.
            raise DuplicateUserError("A user with this email already exists.") from exc
        await self._audit.record(
            AuditEvent(
                action=AuditAction.ORG_ONBOARD,
                actor_type=AuditActorType.platform_admin,
                actor_id=actor.subject_id,
                org_id=organization.id,
                entity_type="organization",
                entity_id=organization.id,
                details={"slug": organization.slug, "name": organization.name},
            )
        )
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
