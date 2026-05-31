"""
Role: DB-backed fixtures for the identity test suite — schema lifecycle, committed
      seed sessions, an HTTP client, and JWT minting helpers.
Used by: every test under tests/identity/ that touches the database or the routes.
Depends on: app.core.database (engine, SessionLocal, scoped_session), app.identity
            models + security.tokens, app.main (the ASGI app).
Key invariants:
  - Per-test DB isolation holds whether the identity tables are Alembic-managed (CI
    runs `alembic upgrade head` before pytest) or absent: the schema fixture creates
    any MISSING tables, then on teardown DROPS the ones it created (fresh DB → leave
    it pristine) or TRUNCATEs the data (pre-existing/migrated schema → keep the tables,
    wipe rows). Either way each test starts clean. FUNCTION-scoped: asyncpg
    connections are bound to the event loop that created them, and the root conftest
    disposes the engine after every test, so a session-scoped async DB fixture would
    cross loops and fail.
  - Seed data is written through a COMMITTED session so later HTTP requests (which
    open their own sessions) can see it. Each test states its own seed explicitly.
  - JWTs are minted with the production encoder so role/audience/expiry behave
    exactly as in real requests — no auth is mocked anywhere in this suite.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import UUID, uuid4

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.base_model import Base
from app.core.database import SessionLocal, engine
from app.identity import models as identity_models  # noqa: F401 — registers tables on Base.metadata
from app.identity.models.organization import Organization
from app.identity.models.platform_admin import PlatformAdmin
from app.identity.models.user import User
from app.identity.principal import Principal
from app.identity.security.password import hash_password
from app.identity.security.tokens import (
    COMPANY_AUDIENCE,
    PLATFORM_AUDIENCE,
    encode_access_token,
)
from app.main import app

# The four identity tables, in dependency order (users FK -> organizations).
_IDENTITY_TABLES = [
    Organization.__table__,
    User.__table__,
    PlatformAdmin.__table__,
    Organization.metadata.tables["refresh_tokens"],
]


def _missing_identity_tables(sync_connection: object) -> list[object]:
    """Return the identity tables that DON'T yet exist on the connected database.

    Once the phase-2 identity migration lands, those tables are managed by Alembic
    and must NOT be dropped by the test fixture — only tables this fixture itself
    creates are torn down (see identity_schema). This is the "only drop what I
    created" guard that keeps a shared dev/CI database safe.
    """
    from sqlalchemy import inspect  # local import: only the sync inspector path needs it

    existing = set(inspect(sync_connection).get_table_names())
    return [table for table in _IDENTITY_TABLES if table.name not in existing]


@pytest_asyncio.fixture
async def identity_schema() -> AsyncIterator[None]:
    """Give each test a clean identity schema, then reset it.

    Function-scoped on purpose (see module docstring). Setup creates any MISSING
    identity tables (checkfirst). Teardown then either:
      - drops the tables this fixture created (fresh DB → leave it pristine), or
      - TRUNCATEs the data if the tables pre-existed (CI applies the migration before
        pytest, so the schema is Alembic-managed — wipe rows, keep the tables).
    This keeps every test isolated whether or not the migration has run.
    """
    async with engine.begin() as connection:
        created_tables = await connection.run_sync(_missing_identity_tables)
        await connection.run_sync(Base.metadata.create_all, tables=created_tables)
    try:
        yield
    finally:
        async with engine.begin() as connection:
            if created_tables:
                await connection.run_sync(Base.metadata.drop_all, tables=created_tables)
            else:
                await connection.execute(
                    text(
                        "TRUNCATE TABLE refresh_tokens, users, platform_admins, "
                        "organizations RESTART IDENTITY CASCADE"
                    )
                )


@pytest_asyncio.fixture
async def db_session(identity_schema: None) -> AsyncIterator[AsyncSession]:
    """Yield a committed-on-success plain session for explicit test seeding.

    Commits on exit so data persists for subsequent HTTP requests in the same test;
    rolls back on error. Repository/service tests also use this session directly.
    """
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest_asyncio.fixture
async def client(identity_schema: None) -> AsyncIterator[AsyncClient]:
    """Yield an HTTP client bound to the in-process ASGI app (tables already created)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


# — Seed helpers (explicit, returned ids let each test state its own setup) —


async def seed_organization(
    session: AsyncSession, *, name: str, slug: str, status: str = "active"
) -> Organization:
    """Insert an organization and flush so its id/created_at are populated."""
    organization = Organization(name=name, slug=slug, status=status)
    session.add(organization)
    await session.flush()
    return organization


async def seed_user(
    session: AsyncSession,
    *,
    org_id: UUID,
    email: str,
    full_name: str,
    role: str,
    password: str = "Test-Pass-123",
    is_active: bool = True,
) -> User:
    """Insert a user (password bcrypt-hashed) and flush to populate server defaults."""
    user = User(
        org_id=org_id,
        email=email,
        full_name=full_name,
        password_hash=hash_password(password),
        role=role,
        is_active=is_active,
    )
    session.add(user)
    await session.flush()
    return user


async def seed_platform_admin(
    session: AsyncSession,
    *,
    email: str,
    full_name: str,
    password: str = "Test-Pass-123",
    is_active: bool = True,
) -> PlatformAdmin:
    """Insert a platform admin (password bcrypt-hashed) and flush."""
    admin = PlatformAdmin(
        email=email,
        full_name=full_name,
        password_hash=hash_password(password),
        is_active=is_active,
    )
    session.add(admin)
    await session.flush()
    return admin


# — Token-minting helpers (production encoder; no mocking) —


def company_token(
    user_id: UUID, org_id: UUID, role: str, ttl_minutes: int = 15
) -> str:
    """Mint a company access token (aud='company') for a user principal."""
    principal = Principal(
        subject_id=user_id, org_id=org_id, role=role, subject_type="user"
    )
    return encode_access_token(
        principal, timedelta(minutes=ttl_minutes), COMPANY_AUDIENCE
    )


def platform_token(admin_id: UUID | None = None, ttl_minutes: int = 15) -> str:
    """Mint a platform access token (aud='platform') for a platform-admin principal."""
    principal = Principal(
        subject_id=admin_id or uuid4(),
        org_id=None,
        role="platform_admin",
        subject_type="platform_admin",
    )
    return encode_access_token(
        principal, timedelta(minutes=ttl_minutes), PLATFORM_AUDIENCE
    )


def bearer(token: str) -> dict[str, str]:
    """Return an Authorization header dict for `token`."""
    return {"Authorization": f"Bearer {token}"}
