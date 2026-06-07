"""
Role: Cross-tenant isolation tests for CompanyRepository — the testing.md non-negotiable at the
      company-graph repo layer, plus the org-scoped uniqueness of the domain match key.
Used by: pytest (tests/entities).
Depends on: the entities conftest (entity_schema + db_session), the model + repo.
Key invariants tested:
  - get_in_org / list_for_org / get_company_id_by_domain are org-scoped.
  - The domain match key is UNIQUE within an org, so the SAME domain in two orgs is two companies.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.models.company import Company, CompanyDomain
from app.entities.repositories.company_repository import CompanyRepository


async def test_get_in_org_other_org_returns_none(db_session: AsyncSession) -> None:
    repo = CompanyRepository(db_session)
    org_a, org_b = uuid4(), uuid4()
    company = await repo.insert(Company(org_id=org_a, name="Globex"))

    assert await repo.get_in_org(company.id, org_b) is None
    assert await repo.get_in_org(company.id, org_a) is not None


async def test_list_for_org_excludes_other_orgs(db_session: AsyncSession) -> None:
    repo = CompanyRepository(db_session)
    org_a, org_b = uuid4(), uuid4()
    await repo.insert(Company(org_id=org_a, name="Globex"))

    assert await repo.list_for_org(org_b) == []
    assert len(await repo.list_for_org(org_a)) == 1


async def test_get_company_id_by_domain_is_org_scoped(db_session: AsyncSession) -> None:
    repo = CompanyRepository(db_session)
    org_a, org_b = uuid4(), uuid4()
    company = await repo.insert(Company(org_id=org_a, name="Globex"))
    await repo.add_domain(
        CompanyDomain(org_id=org_a, company_id=company.id, domain="globex.com", source="imap")
    )

    assert await repo.get_company_id_by_domain(org_a, "globex.com") == company.id
    assert await repo.get_company_id_by_domain(org_b, "globex.com") is None


async def test_same_domain_in_two_orgs_are_distinct_companies(db_session: AsyncSession) -> None:
    repo = CompanyRepository(db_session)
    org_a, org_b = uuid4(), uuid4()
    company_a = await repo.insert(Company(org_id=org_a, name="Globex A"))
    company_b = await repo.insert(Company(org_id=org_b, name="Globex B"))

    await repo.add_domain(CompanyDomain(org_id=org_a, company_id=company_a.id, domain="globex.com"))
    await repo.add_domain(CompanyDomain(org_id=org_b, company_id=company_b.id, domain="globex.com"))

    assert await repo.get_company_id_by_domain(org_a, "globex.com") == company_a.id
    assert await repo.get_company_id_by_domain(org_b, "globex.com") == company_b.id
