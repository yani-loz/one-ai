"""
Role: DB-backed + override fixtures for the connectors test suite — schema lifecycle, a committed
      seed session, an HTTP client whose connector registry is swapped for a fake (no real IMAP),
      and company-JWT helpers.
Used by: every test under tests/connectors/ that touches the DB or the routes. The pure-unit
         tests (cipher, registry, imap connector) don't use these and run without a database.
Depends on: app.core.database (engine/GlobalSessionLocal/runtime_roles_present),
            app.connectors models/base/dependencies, app.identity.security.tokens + principal
            (real JWT minting), app.main (the ASGI app).
Key invariants:
  - Per-test isolation: the schema fixture creates the connector table if missing, then on
    teardown TRUNCATEs it (migrated DB) or DROPs it (fresh DB) — function-scoped, because asyncpg
    connections bind to the per-test event loop (the root conftest disposes the engine each test).
  - The IMAP vendor boundary is NEVER hit in tests: the `client` fixture overrides
    get_connector_registry with a registry whose imap factory returns a stub connector whose
    outcome the test controls via `stub_outcome` — so route tests exercise the real service,
    repo, cipher, and DB, mocking only the network edge (rule: mock at the adapter boundary).
  - JWTs are minted with the production encoder (no auth mocked).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.base_model import Base
from app.connectors.base.connector import BaseConnector, ConnectionCheck
from app.connectors.base.registry import ConnectorRegistry
from app.connectors.dependencies import get_connector_registry
from app.connectors.enums import ConnectorType
from app.connectors.models.connector_connection import ConnectorConnection
from app.core.database import GlobalSessionLocal, engine, runtime_roles_present
from app.identity.principal import Principal
from app.identity.security.tokens import COMPANY_AUDIENCE, encode_access_token
from app.main import app

_CONNECTOR_TABLES = [ConnectorConnection.__table__]


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


@pytest_asyncio.fixture
async def client(
    connector_schema: None, stub_outcome: dict[str, ConnectionCheck]
) -> AsyncIterator[AsyncClient]:
    """Yield an HTTP client whose connector registry is a fake (stub IMAP — no real server)."""

    def _stub_registry() -> ConnectorRegistry:
        registry = ConnectorRegistry()
        registry.register(
            ConnectorType.imap,
            lambda config, secret: _StubConnector(stub_outcome["check"]),
        )
        return registry

    app.dependency_overrides[get_connector_registry] = _stub_registry
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client
    finally:
        app.dependency_overrides.pop(get_connector_registry, None)


def company_token(
    user_id: UUID, org_id: UUID, role: str = "company_admin", ttl_minutes: int = 15
) -> str:
    """Mint a company access token (aud='company') for a user principal."""
    principal = Principal(subject_id=user_id, org_id=org_id, role=role, subject_type="user")
    return encode_access_token(principal, timedelta(minutes=ttl_minutes), COMPANY_AUDIENCE)


def bearer(token: str) -> dict[str, str]:
    """Return an Authorization header dict for `token`."""
    return {"Authorization": f"Bearer {token}"}
