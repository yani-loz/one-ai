"""
DB-backed tests for app.identity.repositories.user_repository.

Covers the org-scoped data-access contract, including the NON-NEGOTIABLE
repository-layer cross-tenant isolation tests (testing.md): an org-scoped read of
another org's row must resolve to None, and a per-org listing must never include
another org's rows. Requires a reachable Postgres; tables created by the
identity_schema fixture.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.enums import UserRole
from app.identity.repositories.user_repository import UserRepository
from tests.identity.conftest import seed_organization, seed_user


async def test_get_by_email_returns_user_across_orgs(db_session: AsyncSession) -> None:
    org = await seed_organization(db_session, name="Acme", slug="acme")
    await seed_user(
        db_session, org_id=org.id, email="ceo@acme.example", full_name="Ada", role=UserRole.member
    )
    repository = UserRepository(db_session)

    found = await repository.get_by_email("ceo@acme.example")

    assert found is not None
    assert found.email == "ceo@acme.example"


async def test_get_by_email_unknown_returns_none(db_session: AsyncSession) -> None:
    repository = UserRepository(db_session)

    found = await repository.get_by_email("nobody@nowhere.example")

    assert found is None


async def test_get_in_org_same_org_returns_user(db_session: AsyncSession) -> None:
    org = await seed_organization(db_session, name="Acme", slug="acme")
    user = await seed_user(
        db_session, org_id=org.id, email="dev@acme.example", full_name="Dev", role=UserRole.member
    )
    repository = UserRepository(db_session)

    found = await repository.get_in_org(user.id, org.id)

    assert found is not None
    assert found.id == user.id


async def test_get_in_org_cross_tenant_returns_none(db_session: AsyncSession) -> None:
    # THE NON-NEGOTIABLE (repo layer): org A reading org B's user resolves to None,
    # so the service can map it to 404 and never leak existence across tenants.
    org_a = await seed_organization(db_session, name="Org A", slug="org-a")
    org_b = await seed_organization(db_session, name="Org B", slug="org-b")
    user_b = await seed_user(
        db_session, org_id=org_b.id, email="secret@b.example",
        full_name="Bob B", role=UserRole.member,
    )
    repository = UserRepository(db_session)

    found = await repository.get_in_org(user_b.id, org_a.id)

    assert found is None


async def test_list_by_org_excludes_other_org_rows(db_session: AsyncSession) -> None:
    # THE NON-NEGOTIABLE (repo layer): a per-org listing must contain ONLY that org's
    # rows — no cross-tenant bleed.
    org_a = await seed_organization(db_session, name="Org A", slug="org-a")
    org_b = await seed_organization(db_session, name="Org B", slug="org-b")
    await seed_user(
        db_session, org_id=org_a.id, email="a1@a.example", full_name="A One", role=UserRole.member
    )
    await seed_user(
        db_session, org_id=org_b.id, email="b1@b.example", full_name="B One", role=UserRole.member
    )
    repository = UserRepository(db_session)

    listed = await repository.list_by_org(org_a.id)

    listed_emails = {user.email for user in listed}
    assert listed_emails == {"a1@a.example"}
    assert "b1@b.example" not in listed_emails


async def test_email_exists_true_when_present(db_session: AsyncSession) -> None:
    org = await seed_organization(db_session, name="Acme", slug="acme")
    await seed_user(
        db_session, org_id=org.id, email="taken@acme.example", full_name="T", role=UserRole.member
    )
    repository = UserRepository(db_session)

    assert await repository.email_exists("taken@acme.example") is True


async def test_email_exists_false_when_absent(db_session: AsyncSession) -> None:
    repository = UserRepository(db_session)

    assert await repository.email_exists("free@acme.example") is False
