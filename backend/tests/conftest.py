"""
Role: Shared pytest fixtures + the tenant-root org seeding helpers for the backend suite.
Used by: all tests under backend/tests/. `register_org`/`seed_org` are imported by the
         connectors/entities suites (and their conftest seed helpers) to satisfy the 0014
         org-root FKs before minting tenant rows under a fresh uuid4 org.
Depends on: httpx, app.main, app.core.database, app.identity.models.organization.
Key invariants:
  - `client` talks to the real ASGI app via in-process transport (no network).
  - DB-backed tests require a reachable Postgres (compose `db` service or CI).
  - Since migration 0014 every tenant-scoped table FKs organizations(id): a test minting an
    org UUID MUST register it (register_org in-transaction, or seed_org committed) before
    inserting tenant rows — otherwise the insert fails with a ForeignKeyViolation.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import (
    GlobalSessionLocal,
    engine,
    global_engine,
    reader_engine,
    tenant_engine,
)
from app.identity.models.organization import Organization
from app.main import app


async def register_org(session: AsyncSession, org_id: UUID) -> None:
    """Insert an organizations row for `org_id` inside `session`'s transaction (idempotent).

    Satisfies the 0014 org-root FKs for tenant rows the same transaction is about to insert.
    The slug is derived from the UUID (unique, matches ck_organizations_slug_format) so
    repeated registrations and parallel orgs never collide; ON CONFLICT DO NOTHING makes
    re-registering the same org a no-op.
    """
    await session.execute(
        pg_insert(Organization.__table__)
        .values(id=org_id, name=f"Test Org {org_id.hex[:12]}", slug=f"org-{org_id.hex}")
        .on_conflict_do_nothing(index_elements=["id"])
    )


async def seed_org(org_id: UUID | None = None) -> UUID:
    """Insert (and COMMIT) an organizations row; return its id (fresh uuid4 by default).

    For tests whose tenant writes happen in a SEPARATE session (HTTP routes, services that
    open their own sessions): the org row is committed immediately so any later transaction
    sees it. Idempotent via register_org.
    """
    org_uuid = org_id if org_id is not None else uuid4()
    async with GlobalSessionLocal() as session:
        await register_org(session, org_uuid)
        await session.commit()
    return org_uuid


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_between_tests() -> AsyncIterator[None]:
    """Dispose ALL FOUR async engines after each test.

    pytest-asyncio runs each test in its own event loop; asyncpg connections are
    bound to the loop that created them. Without disposal, a pooled connection
    from one test is reused by the next test's loop and fails ("Event loop is
    closed"). Post the RLS role split + PF-01 there are four engines (owner +
    tenant + global + reader) — every one must be disposed, or a test that
    touched any pool leaks a connection into the next test's loop.
    """
    yield
    for db_engine in (tenant_engine, global_engine, reader_engine, engine):
        await db_engine.dispose()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Yield an HTTP client bound to the in-process ASGI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
