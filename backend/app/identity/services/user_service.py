"""
Role: Company-admin user management — create/list/update/deactivate users, strictly
      scoped to the caller's org. Holds the user-management business logic (rule A5).
Used by: routes.user_routes; constructed in identity.dependencies on a TENANT session.
Depends on: user_repository, security.password, identity.schemas.user_schemas,
            identity.enums, identity.exceptions, identity.models.user.
Key invariants:
  - EVERY method takes the caller's org_id (from the verified JWT, via the route) and
    passes it to org-scoped repository methods. No path can read/mutate another org's
    user — cross-org targets resolve to UserNotFoundError (-> 404), never a 200-empty
    or existence leak.
  - org_id on a created/updated user comes from the caller's token, never the body.
  - Deactivate is a soft delete (is_active=False); users are never hard-deleted here.
  - email is globally unique; creating a duplicate raises DuplicateUserError (-> 409),
    including the concurrent race (IntegrityError -> 409, not 500).
  - The org's last active company_admin cannot be demoted/deactivated (LastAdminError
    -> 409) — an org can never be locked out of its own user management. The guard is
    CONCURRENCY-SAFE: it locks the active-admin rows FOR UPDATE so two simultaneous
    removals serialize instead of racing to zero admins (DYN-01).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.identity.enums import AuditActorType, UserRole
from app.identity.exceptions import (
    DuplicateUserError,
    LastAdminError,
    UserNotFoundError,
)
from app.identity.models.user import User
from app.identity.principal import Principal
from app.identity.repositories.user_repository import UserRepository
from app.identity.schemas.user_schemas import (
    UserCreateRequest,
    UserResponse,
    UserUpdateRequest,
)
from app.identity.security.password import hash_password_async
from app.identity.security.token_denylist import TokenDenylist
from app.identity.services.audit_service import AuditAction, AuditEvent, AuditService

_ENTITY_USER = "user"


class UserService:
    """Org-scoped CRUD for company users (company_admin only)."""

    def __init__(
        self, users: UserRepository, audit: AuditService, token_denylist: TokenDenylist
    ) -> None:
        """Bind the user repository + audit writer (one TENANT-scoped session) + denylist.

        token_denylist is the process-wide revocation singleton: deactivating a user must
        immediately kill their outstanding access tokens (else a deactivated — possibly
        compromised — account keeps working for up to the <=15min access-token TTL).
        """
        self._users = users
        self._audit = audit
        self._token_denylist = token_denylist

    async def list_users(self, org_id: UUID) -> list[UserResponse]:
        """Return all users in the caller's org (newest first)."""
        users = await self._users.list_by_org(org_id)
        return [UserResponse.model_validate(user) for user in users]

    async def create_user(
        self, org_id: UUID, payload: UserCreateRequest, actor: Principal
    ) -> UserResponse:
        """Create a user in the caller's org from a validated payload.

        Contract: hashes the password (bcrypt, on a worker thread via
        hash_password_async so the event loop stays free), forces org_id to the
        caller's org, persists, and records a `user.create` audit row on the SAME
        session (so the user and its audit row commit together). Returns the created
        user view.

        Raises:
            DuplicateUserError: the email already belongs to a user (-> 409).
        """
        if await self._users.email_exists(payload.email):
            raise DuplicateUserError("A user with this email already exists.")

        user = User(
            org_id=org_id,
            email=payload.email,
            full_name=payload.full_name,
            password_hash=await hash_password_async(payload.password),
            role=payload.role.value,
        )
        try:
            created = await self._users.add(user)
        except IntegrityError as exc:
            # Lost a concurrent race against the users.email UNIQUE constraint after the
            # pre-check passed: surface the documented 409, not an opaque 500 (AUD-05).
            raise DuplicateUserError("A user with this email already exists.") from exc
        # Content-blind: record the target's id + role, NEVER the email — the audit is
        # platform-readable, and a tenant user's email is PII the platform must not see.
        await self._audit.record(
            self._user_event(AuditAction.USER_CREATE, actor, created, {"role": created.role})
        )
        return UserResponse.model_validate(created)

    async def update_user(
        self, org_id: UUID, user_id: UUID, payload: UserUpdateRequest, actor: Principal
    ) -> UserResponse:
        """Apply a partial update to a user in the caller's org.

        Contract: only the provided fields change; org_id is never mutated. A role change
        records `user.role_change`, a deactivation records `user.deactivate`, and a
        reactivation records `user.reactivate` on the same session (committed with the update)
        — both activation directions are audited, mirroring org suspend/reactivate.

        Raises:
            UserNotFoundError: no such user in this org (-> 404; never leaks existence
                of a user in another org).
            LastAdminError: the change would strand the org's last active admin (-> 409).
        """
        user = await self._users.get_in_org(user_id, org_id)
        if user is None:
            raise UserNotFoundError("User not found.")

        await self._guard_last_admin(org_id, user, payload.role, payload.is_active)

        previous_role = user.role
        previous_active = user.is_active
        if payload.full_name is not None:
            user.full_name = payload.full_name
        if payload.role is not None:
            user.role = UserRole(payload.role).value
        if payload.is_active is not None:
            user.is_active = payload.is_active

        await self._users.add(user)
        revoke_access_tokens = user.role != previous_role or (
            previous_active and not user.is_active
        )
        if revoke_access_tokens:
            self._token_denylist.revoke_subject(user.id)
        if user.role != previous_role:
            await self._audit.record(
                self._user_event(
                    AuditAction.USER_ROLE_CHANGE,
                    actor,
                    user,
                    {"from_role": previous_role, "to_role": user.role},
                )
            )
        if previous_active and not user.is_active:
            await self._audit.record(self._user_event(AuditAction.USER_DEACTIVATE, actor, user, {}))
        if not previous_active and user.is_active:
            # Restoring access (possibly admin access) is as audit-worthy as removing it — a
            # DPO/Betriebsrat must see both directions (mirrors org suspend/reactivate).
            await self._audit.record(self._user_event(AuditAction.USER_REACTIVATE, actor, user, {}))
        return UserResponse.model_validate(user)

    async def deactivate_user(self, org_id: UUID, user_id: UUID, actor: Principal) -> None:
        """Soft-delete a user in the caller's org (is_active=False), audited same-tx.

        Raises:
            UserNotFoundError: no such user in this org (-> 404).
            LastAdminError: the user is the org's last active admin (-> 409).
        """
        user = await self._users.get_in_org(user_id, org_id)
        if user is None:
            raise UserNotFoundError("User not found.")
        await self._guard_last_admin(org_id, user, new_role=None, new_is_active=False)
        already_inactive = not user.is_active
        user.is_active = False
        await self._users.add(user)
        # Immediately revoke the user's in-flight access tokens (the denylist) — a soft-delete
        # that left a compromised account's token working for its TTL would defeat the purpose.
        self._token_denylist.revoke_subject(user_id)
        if not already_inactive:
            await self._audit.record(self._user_event(AuditAction.USER_DEACTIVATE, actor, user, {}))

    @staticmethod
    def _user_event(
        action: str, actor: Principal, target: User, details: dict[str, object]
    ) -> AuditEvent:
        """Build a content-blind user-management audit event (target id + role, no email)."""
        return AuditEvent(
            action=action,
            actor_type=AuditActorType.user,
            actor_id=actor.subject_id,
            org_id=target.org_id,
            entity_type=_ENTITY_USER,
            entity_id=target.id,
            details=details,
        )

    async def _guard_last_admin(
        self,
        org_id: UUID,
        user: User,
        new_role: UserRole | None,
        new_is_active: bool | None,
    ) -> None:
        """Block a change that would remove the org's last active company_admin.

        Computes the user's post-change state; if the change turns the org's last active
        admin into a non-admin or an inactive account, raises LastAdminError (-> 409) so
        an org can never be locked out of its own user management (AUD-07). The check is
        concurrency-safe (DYN-01): it locks the active-admin rows FOR UPDATE so two
        simultaneous removals serialize — the loser re-reads the reduced set and 409s
        instead of both stranding the org at zero admins.
        """
        was_active_admin = user.role == UserRole.company_admin.value and user.is_active
        if not was_active_admin:
            return
        resolved_role = new_role.value if new_role is not None else user.role
        resolved_active = new_is_active if new_is_active is not None else user.is_active
        if resolved_role == UserRole.company_admin.value and resolved_active:
            return
        # Lock the active-admin set so a concurrent removal can't pass a stale count.
        active_admin_ids = await self._users.lock_active_admin_ids(org_id)
        if not any(admin_id != user.id for admin_id in active_admin_ids):
            raise LastAdminError("Cannot remove the organization's last active administrator.")
