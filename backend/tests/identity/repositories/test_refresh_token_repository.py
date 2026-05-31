"""
DB-backed tests for app.identity.repositories.refresh_token_repository — persistence
of opaque-token hashes plus the atomic conditional revoke (revoke_by_hash, the AUD-01
single-use primitive). Requires Postgres.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models.refresh_token import RefreshToken
from app.identity.repositories.refresh_token_repository import RefreshTokenRepository


async def _add_token(
    session: AsyncSession, token_hash: str, *, revoked: bool = False
) -> RefreshToken:
    token = RefreshToken(
        subject_id=uuid4(),
        subject_type="user",
        token_hash=token_hash,
        expires_at=datetime.now(UTC) + timedelta(days=7),
        revoked_at=datetime.now(UTC) if revoked else None,
    )
    session.add(token)
    await session.flush()
    return token


async def test_get_by_hash_returns_stored_token(db_session: AsyncSession) -> None:
    await _add_token(db_session, "a" * 64)
    repository = RefreshTokenRepository(db_session)

    found = await repository.get_by_hash("a" * 64)

    assert found is not None
    assert found.token_hash == "a" * 64


async def test_get_by_hash_unknown_returns_none(db_session: AsyncSession) -> None:
    repository = RefreshTokenRepository(db_session)

    found = await repository.get_by_hash("f" * 64)

    assert found is None


async def test_revoke_by_hash_is_single_use(db_session: AsyncSession) -> None:
    # AUD-01 atomicity primitive: the conditional revoke succeeds exactly once; a second
    # revoke of the same token touches zero rows. Rotation relies on this to reject a
    # token that already lost a concurrent race (rowcount == 0 -> invalid).
    await _add_token(db_session, "b" * 64)
    repository = RefreshTokenRepository(db_session)

    first = await repository.revoke_by_hash("b" * 64)
    second = await repository.revoke_by_hash("b" * 64)

    assert first == 1
    assert second == 0


async def test_revoke_by_hash_returns_one_for_active_token(db_session: AsyncSession) -> None:
    await _add_token(db_session, "c" * 64)
    repository = RefreshTokenRepository(db_session)

    affected = await repository.revoke_by_hash("c" * 64)

    assert affected == 1


async def test_revoke_by_hash_unknown_token_returns_zero(db_session: AsyncSession) -> None:
    # Logging out an unknown token is a no-op (no error), so the count is zero.
    repository = RefreshTokenRepository(db_session)

    affected = await repository.revoke_by_hash("d" * 64)

    assert affected == 0


async def test_revoke_by_hash_already_revoked_returns_zero(db_session: AsyncSession) -> None:
    await _add_token(db_session, "e" * 64, revoked=True)
    repository = RefreshTokenRepository(db_session)

    affected = await repository.revoke_by_hash("e" * 64)

    assert affected == 0
