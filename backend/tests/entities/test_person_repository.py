"""
Role: Cross-tenant isolation tests for PersonRepository — the testing.md non-negotiable at the
      person-graph repo layer, plus the org-scoped uniqueness of the email match key.
Used by: pytest (tests/entities).
Depends on: the entities conftest (entity_schema + db_session), the model + repo.
Key invariants tested:
  - get_in_org / list_for_org / get_person_id_by_email are org-scoped (another org sees nothing).
  - The email match key is UNIQUE *within* an org, so the SAME email in two orgs is two people (no
    cross-tenant collision) — the property the whole entity spine depends on.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.models.person import Person, PersonEmail
from app.entities.repositories.person_repository import PersonRepository
from tests.conftest import seed_org


async def test_get_in_org_other_org_returns_none(db_session: AsyncSession) -> None:
    repo = PersonRepository(db_session)
    org_a, org_b = await seed_org(), await seed_org()
    person = await repo.insert(Person(org_id=org_a, display_name="Boyan"))

    assert await repo.get_in_org(person.id, org_b) is None
    assert await repo.get_in_org(person.id, org_a) is not None


async def test_list_for_org_excludes_other_orgs(db_session: AsyncSession) -> None:
    repo = PersonRepository(db_session)
    org_a, org_b = await seed_org(), await seed_org()
    await repo.insert(Person(org_id=org_a, display_name="Boyan"))

    assert await repo.list_for_org(org_b) == []
    assert len(await repo.list_for_org(org_a)) == 1


async def test_get_person_id_by_email_is_org_scoped(db_session: AsyncSession) -> None:
    repo = PersonRepository(db_session)
    org_a, org_b = await seed_org(), await seed_org()
    person = await repo.insert(Person(org_id=org_a, display_name="Boyan"))
    await repo.add_email(
        PersonEmail(org_id=org_a, person_id=person.id, email="boyan@example.com", source="imap")
    )

    assert await repo.get_person_id_by_email(org_a, "boyan@example.com") == person.id
    assert await repo.get_person_id_by_email(org_b, "boyan@example.com") is None


async def test_same_email_in_two_orgs_are_distinct_people(db_session: AsyncSession) -> None:
    repo = PersonRepository(db_session)
    org_a, org_b = await seed_org(), await seed_org()
    person_a = await repo.insert(Person(org_id=org_a))
    person_b = await repo.insert(Person(org_id=org_b))

    # The UNIQUE(org_id, email) match key must NOT collide across tenants.
    await repo.add_email(PersonEmail(org_id=org_a, person_id=person_a.id, email="shared@x.com"))
    await repo.add_email(PersonEmail(org_id=org_b, person_id=person_b.id, email="shared@x.com"))

    assert await repo.get_person_id_by_email(org_a, "shared@x.com") == person_a.id
    assert await repo.get_person_id_by_email(org_b, "shared@x.com") == person_b.id
