"""
Role: UNIQUE match-key rejection + write-path coverage for the entity graph — proves the
      per-org match-key constraints actually REJECT intra-org duplicates (the determinism the
      resolver's get-or-create depends on in 3c), and exercises the alias / person-company writers.
Used by: pytest (tests/entities).
Depends on: the entities conftest (entity_schema + db_session), the models + repos.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.models.company import Company, CompanyDomain, PersonCompany
from app.entities.models.person import Person, PersonAlias, PersonEmail
from app.entities.repositories.company_repository import CompanyRepository
from app.entities.repositories.person_repository import PersonRepository
from tests.conftest import seed_org


async def test_duplicate_email_same_org_rejected(db_session: AsyncSession) -> None:
    repo = PersonRepository(db_session)
    org = await seed_org()
    person_one = await repo.insert(Person(org_id=org))
    person_two = await repo.insert(Person(org_id=org))
    await repo.add_email(PersonEmail(org_id=org, person_id=person_one.id, email="dup@x.com"))

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await repo.add_email(
                PersonEmail(org_id=org, person_id=person_two.id, email="dup@x.com")
            )


async def test_duplicate_domain_same_org_rejected(db_session: AsyncSession) -> None:
    repo = CompanyRepository(db_session)
    org = await seed_org()
    company_one = await repo.insert(Company(org_id=org))
    company_two = await repo.insert(Company(org_id=org))
    await repo.add_domain(CompanyDomain(org_id=org, company_id=company_one.id, domain="dup.com"))

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await repo.add_domain(
                CompanyDomain(org_id=org, company_id=company_two.id, domain="dup.com")
            )


async def test_add_alias_and_link_person_persist(db_session: AsyncSession) -> None:
    person_repo = PersonRepository(db_session)
    company_repo = CompanyRepository(db_session)
    org = await seed_org()
    person = await person_repo.insert(Person(org_id=org))
    company = await company_repo.insert(Company(org_id=org, name="Globex"))

    alias = await person_repo.add_alias(PersonAlias(org_id=org, person_id=person.id, alias="B.B."))
    link = await company_repo.link_person(
        PersonCompany(org_id=org, person_id=person.id, company_id=company.id)
    )

    assert alias.id is not None
    assert link.id is not None
