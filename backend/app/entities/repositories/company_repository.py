"""
Role: Data access for the company graph (company / company_domain / person_company) — no business
      decisions (rule A5). Matching/creation logic lives in the resolver (slice 3c).
Used by: the entity resolver + connector ingest; constructed on the caller's tenant session.
Depends on: app.entities.models.company, SQLAlchemy async.
Key invariants:
  - EVERY read is org-scoped. `get_company_id_by_domain` expects the NORMALIZED domain — the
    deterministic match-key lookup behind get-or-create (generic free-mail domains are excluded by
    the resolver, not stored here).
  - `company` is the EXTERNAL org, never the tenant (`org_id`).
  - The caller owns the transaction; methods only add/flush.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.models.company import Company, CompanyDomain, PersonCompany


class CompanyRepository:
    """Org-scoped persistence for companies, their domains, and person links."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to the caller's tenant-scoped session."""
        self._session = session

    async def insert(self, company: Company) -> Company:
        """Stage a new company and flush to populate the server-generated id."""
        self._session.add(company)
        await self._session.flush()
        return company

    async def get_in_org(self, company_id: UUID, org_id: UUID) -> Company | None:
        """Load a company by id iff it belongs to `org_id`, else None."""
        result = await self._session.execute(
            select(Company).where(Company.id == company_id, Company.org_id == org_id)
        )
        return result.scalar_one_or_none()

    async def list_for_org(self, org_id: UUID) -> list[Company]:
        """Return all of one org's companies, newest-first."""
        result = await self._session.execute(
            select(Company)
            .where(Company.org_id == org_id)
            .order_by(Company.created_at.desc(), Company.id.desc())
        )
        return list(result.scalars().all())

    async def get_company_id_by_domain(self, org_id: UUID, domain: str) -> UUID | None:
        """Return the company owning `domain` (NORMALIZED) in this org — the match-key lookup."""
        result = await self._session.execute(
            select(CompanyDomain.company_id).where(
                CompanyDomain.org_id == org_id, CompanyDomain.domain == domain
            )
        )
        return result.scalar_one_or_none()

    async def add_domain(self, company_domain: CompanyDomain) -> CompanyDomain:
        """Attach a normalized domain to a company."""
        self._session.add(company_domain)
        await self._session.flush()
        return company_domain

    async def link_person(self, link: PersonCompany) -> PersonCompany:
        """Link a person to a company (caller sets org_id/person_id/company_id)."""
        self._session.add(link)
        await self._session.flush()
        return link
