"""
Repo-layer tests for the entities erasure hook (erase_entity_graph_for_org) — real DB.

The cross-tenant negative is the headline: deleting org A's entity graph must not touch ONE
org B row in ANY of the six tables (the hook's own org-scoped SQL is the only containment on
the RLS-exempt erasure session), and the returned per-table counts must be exactly A's rows.
Uses the entities conftest (entity_schema manages all six tables). Requires Postgres.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.models.company import Company, CompanyDomain, PersonCompany
from app.entities.models.person import Person, PersonAlias, PersonEmail
from app.entities.repositories.entity_erasure_repository import erase_entity_graph_for_org
from tests.conftest import register_org

_ENTITY_TABLE_NAMES = (
    "person_company",
    "company_domain",
    "person_alias",
    "person_email",
    "company",
    "person",
)


async def _seed_entity_graph(session: AsyncSession, org_id: UUID, tag: str) -> None:
    """Seed ONE row per entity-graph table for `org_id` (values distinct per `tag`).

    Registers the org row first (idempotent) — 0014's org-root FK rejects phantom tenants.
    """
    await register_org(session, org_id)
    person = Person(org_id=org_id, display_name=f"{tag} Person")
    company = Company(org_id=org_id, name=f"{tag} GmbH")
    session.add_all([person, company])
    await session.flush()
    session.add_all(
        [
            PersonEmail(org_id=org_id, person_id=person.id, email=f"contact@{tag}.example"),
            PersonAlias(org_id=org_id, person_id=person.id, alias=f"{tag} Alias"),
            CompanyDomain(org_id=org_id, company_id=company.id, domain=f"{tag}.example"),
            PersonCompany(org_id=org_id, person_id=person.id, company_id=company.id),
        ]
    )
    await session.flush()


async def _org_row_count(session: AsyncSession, table: str, org_id: UUID) -> int:
    """Count `table` rows belonging to `org_id` (table names are fixed, test-owned)."""
    result = await session.execute(
        text(f"SELECT count(*) FROM {table} WHERE org_id=:o"),  # noqa: S608 — fixed names
        {"o": str(org_id)},
    )
    return result.scalar_one()


async def test_erase_entity_graph_for_org_deletes_a_and_spares_b(
    db_session: AsyncSession,
) -> None:
    # Cross-tenant negative: erasing A's entity graph leaves EVERY B row in EVERY table, and
    # the returned counts are exactly A's one-row-per-table seed (B never inflates them).
    org_a, org_b = uuid4(), uuid4()
    await _seed_entity_graph(db_session, org_a, "acme")
    await _seed_entity_graph(db_session, org_b, "globex")

    counts = await erase_entity_graph_for_org(org_a, db_session)

    assert counts == {table: 1 for table in _ENTITY_TABLE_NAMES}
    for table in _ENTITY_TABLE_NAMES:
        assert await _org_row_count(db_session, table, org_a) == 0, f"{table}: org A survived"
        assert await _org_row_count(db_session, table, org_b) == 1, f"{table}: org B touched"


async def test_erase_entity_graph_for_org_no_rows_returns_zero_counts(
    db_session: AsyncSession,
) -> None:
    # Idempotent on an org with no entity graph: all-zero per-table counts, no error.
    counts = await erase_entity_graph_for_org(uuid4(), db_session)

    assert counts == {table: 0 for table in _ENTITY_TABLE_NAMES}
