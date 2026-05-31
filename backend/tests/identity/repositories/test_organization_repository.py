"""
DB-backed tests for app.identity.repositories.organization_repository — org lookups
and the user-count rollup that backs the platform metadata listing. Requires Postgres.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.enums import UserRole
from app.identity.repositories.organization_repository import OrganizationRepository
from tests.identity.conftest import seed_organization, seed_user


async def test_get_by_slug_returns_organization(db_session: AsyncSession) -> None:
    await seed_organization(db_session, name="Acme", slug="acme")
    repository = OrganizationRepository(db_session)

    found = await repository.get_by_slug("acme")

    assert found is not None
    assert found.name == "Acme"


async def test_get_by_slug_unknown_returns_none(db_session: AsyncSession) -> None:
    repository = OrganizationRepository(db_session)

    found = await repository.get_by_slug("ghost")

    assert found is None


async def test_get_by_id_returns_organization(db_session: AsyncSession) -> None:
    org = await seed_organization(db_session, name="Acme", slug="acme")
    repository = OrganizationRepository(db_session)

    found = await repository.get_by_id(org.id)

    assert found is not None
    assert found.id == org.id


async def test_list_all_with_user_counts_counts_per_org(db_session: AsyncSession) -> None:
    org_with_users = await seed_organization(db_session, name="Busy", slug="busy")
    await seed_organization(db_session, name="Empty", slug="empty")
    await seed_user(
        db_session, org_id=org_with_users.id, email="u1@busy.example",
        full_name="U1", role=UserRole.member,
    )
    await seed_user(
        db_session, org_id=org_with_users.id, email="u2@busy.example",
        full_name="U2", role=UserRole.member,
    )
    repository = OrganizationRepository(db_session)

    counts = {org.slug: count for org, count in await repository.list_all_with_user_counts()}

    assert counts["busy"] == 2
    assert counts["empty"] == 0
