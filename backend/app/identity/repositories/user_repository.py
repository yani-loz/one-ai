"""
Role: Data access for the User table (no business decisions — rule A5).
Used by: AuthService (login lookup), UserService (admin CRUD), PlatformAuthService
         (create the org's first admin).
Depends on: app.identity.models.user.
Key invariants:
  - EVERY method except get_by_email and add is ORG-SCOPED: it takes an org_id and
    filters by it, so no query path can return or mutate another org's rows. This is
    the application-layer half of tenant isolation (security.md).
  - get_by_email is the ONE intentionally non-org-scoped read — used only by login,
    where email is globally unique and the password gates access. It is named so this
    is unmistakable.
  - add operates within a tenant-scoped session whose org GUC is already bound.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.enums import UserRole
from app.identity.models.user import User


class UserRepository:
    """Persistence operations for users. Org-scoped except where explicitly noted."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session.

        For org-scoped methods this is a tenant session whose org_id GUC matches the
        org_id argument; for get_by_email (login) it is the plain pre-auth session.
        """
        self._session = session

    async def get_by_email(self, email: str) -> User | None:
        """Return the user with `email`, or None. NON-ORG-SCOPED — login lookup only.

        Email is globally unique (one user = one org), so this resolves a single user
        across all orgs; the caller MUST still verify the password before trusting it.
        """
        result = await self._session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_by_subject_id(self, user_id: UUID) -> User | None:
        """Return the user with `user_id`, or None. NON-ORG-SCOPED — refresh path only.

        Used by token rotation, where the subject id comes from a server-issued,
        already-validated refresh-token row (not from client input), so resolving the
        user across orgs without an org filter is safe. All ADMIN-facing reads use the
        org-scoped get_in_org instead.
        """
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_in_org(self, user_id: UUID, org_id: UUID) -> User | None:
        """Return user `user_id` IFF it belongs to `org_id`, else None.

        Cross-org lookups resolve to None — the caller maps that to 404 so existence
        in another org is never revealed.
        """
        result = await self._session.execute(
            select(User).where(User.id == user_id, User.org_id == org_id)
        )
        return result.scalar_one_or_none()

    async def list_by_org(self, org_id: UUID) -> list[User]:
        """Return all users in `org_id`, newest first by creation time."""
        result = await self._session.execute(
            select(User).where(User.org_id == org_id).order_by(User.created_at.desc())
        )
        return list(result.scalars().all())

    async def email_exists(self, email: str) -> bool:
        """Return True if any user already has `email` (global uniqueness check)."""
        result = await self._session.execute(select(User.id).where(User.email == email))
        return result.scalar_one_or_none() is not None

    async def lock_active_admin_ids(self, org_id: UUID) -> list[UUID]:
        """Return the ids of active company_admins in `org_id`, LOCKING them FOR UPDATE.

        The row locks serialize concurrent last-admin guards (DYN-01): a second
        transaction removing a *different* admin blocks here until the first commits,
        then re-reads the now-smaller active-admin set (READ COMMITTED re-check). The
        ORDER BY id gives both transactions the SAME lock order, so they queue instead
        of deadlocking. Aggregates can't combine with FOR UPDATE, so the caller counts
        the returned ids.
        """
        result = await self._session.execute(
            select(User.id)
            .where(
                User.org_id == org_id,
                User.role == UserRole.company_admin.value,
                User.is_active.is_(True),
            )
            .order_by(User.id)
            .with_for_update()
        )
        return list(result.scalars().all())

    async def add(self, user: User) -> User:
        """Stage a new user for insert and flush to populate server defaults."""
        self._session.add(user)
        await self._session.flush()
        return user

    async def delete_all_in_org(self, org_id: UUID) -> int:
        """Hard-delete every user in `org_id` (GDPR erasure); return the rows deleted.

        Destructive + org-scoped. Run AFTER the org's refresh tokens are deleted (those key
        on these users' ids). The data subjects' email/full_name/password_hash are removed
        with the row.
        """
        result = await self._session.execute(delete(User).where(User.org_id == org_id))
        return result.rowcount or 0
