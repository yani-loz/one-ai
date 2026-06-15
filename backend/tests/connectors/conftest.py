"""
Role: DB-backed + override fixtures for the connectors test suite — schema lifecycle, a committed
      seed session, an HTTP client whose connector registry is swapped for a fake (no real IMAP),
      and company-JWT helpers. Covers the CO-01 authorization plane too (entitlement / policy /
      override / consent / users), with seed helpers + a /me client whose sync spawner is captured.
Used by: every test under tests/connectors/ that touches the DB or the routes. The pure-unit
         tests (cipher, registry, imap connector) don't use these and run without a database.
Depends on: app.core.database (engine/GlobalSessionLocal/runtime_roles_present),
            app.connectors models/base/dependencies, app.identity.security.tokens + principal
            (real JWT minting), app.identity.models.user + security.password (seed_user for the
            owner/override/consent composite FK), app.main (the ASGI app).
Key invariants:
  - Per-test isolation: the schema fixture creates any missing connector/CO-01/user table, then on
    teardown TRUNCATEs them (migrated DB) or DROPs them (fresh DB) — function-scoped, because
    asyncpg connections bind to the per-test event loop (the root conftest disposes the engine each
    test). The truncate set includes users + the email Layer-1 tables so a CO-01 test never inherits
    another test's owner/connection rows.
  - The IMAP vendor boundary is NEVER hit in tests: the `client` (and `me_client`) fixtures override
    get_connector_registry with a registry whose imap factory returns a stub connector whose
    outcome the test controls via `stub_outcome` — so route tests exercise the real service,
    repo, cipher, and DB, mocking only the network edge (rule: mock at the adapter boundary).
  - CO-01 ownership: a user-owned connection / override / consent FKs users(org_id, id). Tests that
    self-connect MUST seed_user(owner) first, or the insert fails with a ForeignKeyViolation.
  - JWTs are minted with the production encoder (no auth mocked); platform_token mints
    aud='platform'.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Coroutine
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.base_model import Base
from app.connectors.base.connector import BaseConnector, ConnectionCheck
from app.connectors.base.registry import ConnectorRegistry
from app.connectors.dependencies import get_connector_registry, get_sync_task_spawner
from app.connectors.enums import ConnectorType
from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient
from app.connectors.models.connector_connection import ConnectorConnection
from app.connectors.models.connector_consent import ConnectorConsent
from app.connectors.models.connector_entitlement import ConnectorEntitlement
from app.connectors.models.connector_policy import ConnectorPolicy
from app.connectors.models.connector_policy_override import ConnectorPolicyOverride
from app.core.database import GlobalSessionLocal, engine, runtime_roles_present
from app.identity.models.user import User
from app.identity.principal import Principal
from app.identity.security.tokens import (
    COMPANY_AUDIENCE,
    PLATFORM_AUDIENCE,
    encode_access_token,
)
from app.main import app

# Truncated/created per test. Order is irrelevant (TRUNCATE ... CASCADE), but the email children
# precede their parent connection for the create branch's drop_all dependency sort. users is the
# composite-FK target for owner/override/consent; entitlement/policy/override/consent are the CO-01
# authorization plane. All exist on the migrated (0018) DB, so the fixture truncates rather than
# drops them.
_CONNECTOR_TABLES = [
    EmailAttachment.__table__,
    EmailRecipient.__table__,
    EmailMessage.__table__,
    ConnectorConsent.__table__,
    ConnectorPolicyOverride.__table__,
    ConnectorPolicy.__table__,
    ConnectorEntitlement.__table__,
    ConnectorConnection.__table__,
    User.__table__,
]


def _missing_connector_tables(sync_connection: object) -> list[object]:
    """Return the connector tables that don't yet exist on the connected database."""
    from sqlalchemy import inspect

    existing = set(inspect(sync_connection).get_table_names())
    return [table for table in _CONNECTOR_TABLES if table.name not in existing]


@pytest_asyncio.fixture
async def connector_schema() -> AsyncIterator[None]:
    """Give each test a clean connector schema, then reset it (truncate or drop)."""
    if not await runtime_roles_present():
        pytest.skip(
            "Runtime DB roles missing — run `alembic upgrade head` then "
            "`python -m scripts.provision_roles` before the DB suite."
        )
    async with engine.begin() as connection:
        created = await connection.run_sync(_missing_connector_tables)
        await connection.run_sync(Base.metadata.create_all, tables=created)
    try:
        yield
    finally:
        async with engine.begin() as connection:
            pre_existing = [table for table in _CONNECTOR_TABLES if table not in created]
            if pre_existing:
                names = ", ".join(table.name for table in pre_existing)
                await connection.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
            if created:
                await connection.run_sync(Base.metadata.drop_all, tables=created)


@pytest_asyncio.fixture
async def db_session(connector_schema: None) -> AsyncIterator[AsyncSession]:
    """Yield a committed-on-success plain session for explicit test seeding."""
    async with GlobalSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest.fixture
def stub_outcome() -> dict[str, ConnectionCheck]:
    """Mutable holder for the stub connector's verify result (default: success)."""
    return {"check": ConnectionCheck(ok=True, message="Connection verified.")}


class _StubConnector(BaseConnector):
    """A connector that returns a pre-set ConnectionCheck without any network I/O."""

    def __init__(self, outcome: ConnectionCheck) -> None:
        self._outcome = outcome

    @property
    def connector_type(self) -> ConnectorType:
        return ConnectorType.imap

    async def verify_connection(self) -> ConnectionCheck:
        return self._outcome


def _make_stub_registry(check_holder: dict[str, ConnectionCheck]) -> ConnectorRegistry:
    """Build a registry whose imap factory returns the stub connector (the vendor mock boundary)."""
    registry = ConnectorRegistry()
    registry.register(
        ConnectorType.imap,
        lambda config, secret: _StubConnector(check_holder["check"]),
    )
    return registry


@pytest_asyncio.fixture
async def client(
    connector_schema: None, stub_outcome: dict[str, ConnectionCheck]
) -> AsyncIterator[AsyncClient]:
    """Yield an HTTP client whose connector registry is a fake (stub IMAP — no real server)."""
    app.dependency_overrides[get_connector_registry] = lambda: _make_stub_registry(stub_outcome)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client
    finally:
        app.dependency_overrides.pop(get_connector_registry, None)


@pytest.fixture
def spawn_calls() -> list[str]:
    """Collect the labels POST /me/.../sync would have spawned (the runner never runs in-test)."""
    return []


@pytest_asyncio.fixture
async def me_client(
    connector_schema: None, stub_outcome: dict[str, ConnectionCheck], spawn_calls: list[str]
) -> AsyncIterator[AsyncClient]:
    """Yield an HTTP client for the /me plane: stub registry AND a captured sync spawner.

    Overrides BOTH the vendor boundary (no real IMAP) and the background-task spawner (a /me sync
    claims + audits via the real route→service→DB path, but the runner coroutine is closed, never
    driven) — mirroring tests/connectors/sync/test_sync_routes.py's sync_client.
    """

    def _capturing_spawner() -> object:
        def spawn(coro: Coroutine[Any, Any, None], *, label: str) -> None:
            spawn_calls.append(label)
            coro.close()  # assert the spawn happened; don't drive the background runner
            return None

        return spawn

    app.dependency_overrides[get_connector_registry] = lambda: _make_stub_registry(stub_outcome)
    app.dependency_overrides[get_sync_task_spawner] = _capturing_spawner
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client
    finally:
        app.dependency_overrides.pop(get_connector_registry, None)
        app.dependency_overrides.pop(get_sync_task_spawner, None)


def company_token(
    user_id: UUID, org_id: UUID, role: str = "company_admin", ttl_minutes: int = 15
) -> str:
    """Mint a company access token (aud='company') for a user principal."""
    principal = Principal(subject_id=user_id, org_id=org_id, role=role, subject_type="user")
    return encode_access_token(principal, timedelta(minutes=ttl_minutes), COMPANY_AUDIENCE)


def platform_token(admin_id: UUID | None = None, ttl_minutes: int = 15) -> str:
    """Mint a platform access token (aud='platform') for a platform-admin principal."""
    principal = Principal(
        subject_id=admin_id or uuid4(),
        org_id=None,
        role="platform_admin",
        subject_type="platform_admin",
    )
    return encode_access_token(principal, timedelta(minutes=ttl_minutes), PLATFORM_AUDIENCE)


def bearer(token: str) -> dict[str, str]:
    """Return an Authorization header dict for `token`."""
    return {"Authorization": f"Bearer {token}"}
