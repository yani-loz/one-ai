"""
Role: DB-backed fixtures for the entities test suite — schema lifecycle + a committed session.
Used by: tests under tests/entities/ that exercise the person/company repositories (real DB).
Depends on: app.core.database (engine/GlobalSessionLocal/runtime_roles_present),
            app.entities.models.
Key invariants:
  - Per-test isolation: creates MISSING entity tables, then on teardown TRUNCATEs pre-existing ones
    (migrated DB) and DROPs created ones (fresh DB). Function-scoped (asyncpg binds to the per-test
    event loop; the root conftest disposes the engine each test).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.base_model import Base
from app.core.database import GlobalSessionLocal, engine, runtime_roles_present
from app.entities.models.company import Company, CompanyDomain, PersonCompany
from app.entities.models.person import Person, PersonAlias, PersonEmail

# Parents before children (create order). Teardown TRUNCATE ... CASCADE handles FK order.
_ENTITY_TABLES = [
    Person.__table__,
    Company.__table__,
    PersonEmail.__table__,
    PersonAlias.__table__,
    CompanyDomain.__table__,
    PersonCompany.__table__,
]


def _missing_entity_tables(sync_connection: object) -> list[object]:
    """Return the entity tables that don't yet exist on the connected database."""
    from sqlalchemy import inspect

    existing = set(inspect(sync_connection).get_table_names())
    return [table for table in _ENTITY_TABLES if table.name not in existing]


@pytest_asyncio.fixture
async def entity_schema() -> AsyncIterator[None]:
    """Give each test a clean entity schema, then reset it (truncate or drop)."""
    if not await runtime_roles_present():
        pytest.skip(
            "Runtime DB roles missing — run `alembic upgrade head` then "
            "`python -m scripts.provision_roles` before the DB suite."
        )
    async with engine.begin() as connection:
        created = await connection.run_sync(_missing_entity_tables)
        await connection.run_sync(Base.metadata.create_all, tables=created)
    try:
        yield
    finally:
        async with engine.begin() as connection:
            pre_existing = [table for table in _ENTITY_TABLES if table not in created]
            if pre_existing:
                names = ", ".join(table.name for table in pre_existing)
                await connection.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
            if created:
                await connection.run_sync(Base.metadata.drop_all, tables=created)


@pytest_asyncio.fixture
async def db_session(entity_schema: None) -> AsyncIterator[AsyncSession]:
    """Yield a committed-on-success plain session for direct repository tests."""
    async with GlobalSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
