"""
Role: Data access for the refresh_tokens table (no business decisions — rule A5).
Used by: AuthService + PlatformAuthService (issue / rotate / revoke refresh tokens).
Depends on: app.identity.models.refresh_token.
Key invariants:
  - Pure persistence: lookup by hash, insert, atomic conditional revoke. The
    "usable?" decision (not expired, right domain) lives in the service; the
    single-use revoke is done atomically here (revoke_by_hash) so rotation cannot race.
  - Lookups are by token_hash (sha256 hex); the raw token never reaches this layer.
  - subject_id + subject_type identify the owner; tokens span both auth domains, so
    there is no org filter here (a platform admin's token has no org).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models.refresh_token import RefreshToken
from app.identity.models.user import User

_USER_SUBJECT_TYPE = "user"


class RefreshTokenRepository:
    """Persistence operations for refresh tokens."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session (plain for platform, scoped for users)."""
        self._session = session

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        """Return the refresh-token row whose token_hash matches, or None."""
        result = await self._session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def add(self, refresh_token: RefreshToken) -> RefreshToken:
        """Stage a new refresh-token row for insert and flush."""
        self._session.add(refresh_token)
        await self._session.flush()
        return refresh_token

    async def revoke_by_hash(self, token_hash: str) -> int:
        """Idempotently revoke the token with `token_hash`; return rows affected.

        Only rows not yet revoked are touched, so logging out an already-revoked or
        unknown token is a no-op (returns 0) rather than an error.
        """
        result = await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == token_hash, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        return result.rowcount or 0

    async def delete_for_org_users(self, org_id: UUID) -> int:
        """Hard-delete every refresh token owned by `org_id`'s users (erasure); return count.

        Keyed on subject_id IN (the org's user ids), so it MUST run BEFORE those users are
        deleted (afterwards the subquery is empty). Platform-admin tokens
        (subject_type='platform_admin') are Ethera staff and are never touched.
        """
        org_user_ids = select(User.id).where(User.org_id == org_id).scalar_subquery()
        result = await self._session.execute(
            delete(RefreshToken).where(
                RefreshToken.subject_type == _USER_SUBJECT_TYPE,
                RefreshToken.subject_id.in_(org_user_ids),
            )
        )
        return result.rowcount or 0
