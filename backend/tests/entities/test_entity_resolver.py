"""
Role: Integration tests for the deterministic EntityResolver against a real DB — get-or-create
      idempotency, the exclusion guards (role mailbox → no person, generic domain → no company),
      internal/external marking, the seen-window, and the NON-NEGOTIABLE cross-tenant isolation.
Used by: pytest (tests/entities). Real DB via the entities conftest (entity_schema + db_session).
Depends on: app.entities.services.entity_resolver + the entity repositories/models.
Key invariants tested:
  - The same email in two orgs yields two DISTINCT persons; no org sees the other's graph.
  - A role mailbox creates no person but DOES observe its company (DQ-D01); a generic-domain person
    creates no company; allow_person=False observes the company without a person (DQ-C01/C02).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.models.company import Company, PersonCompany
from app.entities.models.person import Person, PersonAlias, PersonEmail
from app.entities.repositories.company_repository import CompanyRepository
from app.entities.repositories.person_repository import PersonRepository
from app.entities.services.entity_resolver import EntityResolver

MAILBOX = "owner@acme.com"


def _resolver(session: AsyncSession, mailbox: str = MAILBOX) -> EntityResolver:
    return EntityResolver(session, mailbox_address=mailbox, source="imap")


async def _count(session: AsyncSession, model: type, org_id) -> int:
    result = await session.execute(
        select(func.count()).select_from(model).where(model.org_id == org_id)
    )
    return result.scalar_one()


async def _display_name(session: AsyncSession, person_id) -> str | None:
    """Read display_name from the column (no ORM identity-map staleness after an UPDATE)."""
    return (
        await session.execute(select(Person.display_name).where(Person.id == person_id))
    ).scalar_one()


async def test_resolve_business_address_creates_person_company_and_link(
    db_session: AsyncSession,
) -> None:
    org = uuid4()
    resolver = _resolver(db_session)

    person_id = await resolver.resolve_participant(org, "boyan@globex.com", display_name="Boyan")

    assert person_id is not None
    assert await _count(db_session, Person, org) == 1
    assert await _count(db_session, Company, org) == 1
    assert await _count(db_session, PersonCompany, org) == 1


async def test_resolve_same_email_twice_returns_same_person(db_session: AsyncSession) -> None:
    org = uuid4()
    resolver = _resolver(db_session)

    first = await resolver.resolve_participant(org, "boyan@globex.com")
    second = await resolver.resolve_participant(org, "BOYAN@globex.com")  # case-variant

    assert first == second
    assert await _count(db_session, Person, org) == 1
    assert await _count(db_session, PersonEmail, org) == 1
    # Re-resolve must also keep the company + link deduped, not just the person.
    assert await _count(db_session, Company, org) == 1
    assert await _count(db_session, PersonCompany, org) == 1


async def test_resolve_role_address_creates_company_but_no_person(db_session: AsyncSession) -> None:
    # DQ-D01: a role/shared mailbox is not a person, but its non-generic domain is still observed as
    # a company — a counterparty contacted only at info@ must not vanish from the graph.
    org = uuid4()
    resolver = _resolver(db_session)

    result = await resolver.resolve_participant(org, "info@globex.com")

    assert result is None  # role address → no person
    assert await _count(db_session, Person, org) == 0
    assert await _count(db_session, Company, org) == 1  # ...but globex.com IS observed


async def test_resolve_disallowed_person_observes_company_only(db_session: AsyncSession) -> None:
    # DQ-C01/C02: an automated sender or a reply_to/sender-only routing identity is not a person
    # (allow_person=False), but its domain is still observed as a company.
    org = uuid4()
    resolver = _resolver(db_session)

    result = await resolver.resolve_participant(org, "newsletter@globex.com", allow_person=False)

    assert result is None
    assert await _count(db_session, Person, org) == 0
    assert await _count(db_session, Company, org) == 1


async def test_resolve_role_company_observation_is_org_scoped(db_session: AsyncSession) -> None:
    # Cross-tenant non-negotiable on the NEW company-observation path: a role address observed in
    # two orgs mints TWO distinct companies; neither org sees the other's row.
    org_a, org_b = uuid4(), uuid4()
    resolver_a = _resolver(db_session, mailbox="owner@acme.com")
    resolver_b = _resolver(db_session, mailbox="owner@beta.com")

    await resolver_a.resolve_participant(org_a, "info@globex.com")  # role → company only (D01)
    await resolver_b.resolve_participant(org_b, "info@globex.com")

    assert await _count(db_session, Company, org_a) == 1
    assert await _count(db_session, Company, org_b) == 1
    companies_a = await CompanyRepository(db_session).list_for_org(org_a)
    assert all(company.org_id == org_a for company in companies_a)  # no org_b rows bleed in


async def test_resolve_generic_domain_creates_person_but_no_company(
    db_session: AsyncSession,
) -> None:
    org = uuid4()
    resolver = _resolver(db_session)

    person_id = await resolver.resolve_participant(org, "private.person@gmail.com")

    assert person_id is not None
    assert await _count(db_session, Person, org) == 1
    assert await _count(db_session, Company, org) == 0  # gmail.com is not a company


async def test_resolve_marks_own_domain_internal_and_others_external(
    db_session: AsyncSession,
) -> None:
    org = uuid4()
    resolver = _resolver(db_session)
    people = PersonRepository(db_session)

    internal_id = await resolver.resolve_participant(org, "colleague@acme.com")
    external_id = await resolver.resolve_participant(org, "client@globex.com")

    internal = await people.get_in_org(internal_id, org)  # type: ignore[arg-type]
    external = await people.get_in_org(external_id, org)  # type: ignore[arg-type]
    assert internal is not None and internal.is_internal is True
    assert external is not None and external.is_internal is False


async def test_resolve_two_people_same_domain_share_one_company(db_session: AsyncSession) -> None:
    org = uuid4()
    resolver = _resolver(db_session)

    await resolver.resolve_participant(org, "a@globex.com")
    await resolver.resolve_participant(org, "b@globex.com")

    assert await _count(db_session, Person, org) == 2
    assert await _count(db_session, Company, org) == 1  # one company, deduped by domain
    assert await _count(db_session, PersonCompany, org) == 2  # both linked


async def test_resolve_extends_seen_window_order_independent(db_session: AsyncSession) -> None:
    org = uuid4()
    resolver = _resolver(db_session)
    people = PersonRepository(db_session)
    early = datetime(2024, 1, 1, tzinfo=UTC)
    late = datetime(2025, 1, 1, tzinfo=UTC)

    # Ingest the LATER email first, then the earlier — the window must still converge correctly.
    pid = await resolver.resolve_participant(org, "boyan@globex.com", seen_at=late)
    await resolver.resolve_participant(org, "boyan@globex.com", seen_at=early)

    person = await people.get_in_org(pid, org)  # type: ignore[arg-type]
    assert person is not None
    assert person.first_seen_at == early
    assert person.last_seen_at == late


async def test_resolve_same_email_two_orgs_distinct_persons(db_session: AsyncSession) -> None:
    # Cross-tenant non-negotiable: identical address in two orgs → two separate persons, no leak.
    org_a, org_b = uuid4(), uuid4()
    resolver_a = _resolver(db_session, mailbox="owner@acme.com")
    resolver_b = _resolver(db_session, mailbox="owner@beta.com")

    person_a = await resolver_a.resolve_participant(org_a, "shared@globex.com")
    person_b = await resolver_b.resolve_participant(org_b, "shared@globex.com")

    assert person_a != person_b
    companies_a = await CompanyRepository(db_session).list_for_org(org_a)
    assert all(company.org_id == org_a for company in companies_a)  # no org_b rows bleed in
    assert await _count(db_session, Person, org_a) == 1
    assert await _count(db_session, Person, org_b) == 1


async def test_resolve_invalid_address_returns_none(db_session: AsyncSession) -> None:
    org = uuid4()
    resolver = _resolver(db_session)

    assert await resolver.resolve_participant(org, "not-an-email") is None
    assert await _count(db_session, Person, org) == 0


async def test_resolve_empty_localpart_or_domain_returns_none(db_session: AsyncSession) -> None:
    # The "@"-only guard was too weak: an empty local-part or domain must not mint a bogus person.
    org = uuid4()
    resolver = _resolver(db_session)

    for address in ["@globex.com", "boyan@", "@", "  "]:
        assert await resolver.resolve_participant(org, address) is None
    assert await _count(db_session, Person, org) == 0
    assert await _count(db_session, Company, org) == 0


async def test_resolve_backfills_blank_display_name_on_later_sighting(
    db_session: AsyncSession,
) -> None:
    # DQ-K04: a person first seen as a bare address is repaired when a named sighting lands; a later
    # blank/worse name never overwrites the good one (first non-empty wins).
    org = uuid4()
    resolver = _resolver(db_session)

    pid = await resolver.resolve_participant(org, "boyan@globex.com")  # no name
    assert await _display_name(db_session, pid) is None

    await resolver.resolve_participant(org, "boyan@globex.com", display_name="Boyan Petrov")
    assert await _display_name(db_session, pid) == "Boyan Petrov"  # back-filled

    await resolver.resolve_participant(org, "boyan@globex.com", display_name="")
    assert await _display_name(db_session, pid) == "Boyan Petrov"  # not overwritten


async def test_resolve_records_deduped_aliases(db_session: AsyncSession) -> None:
    # DQ-K04: each DISTINCT name seen becomes a person_alias; a repeat dedups (UNIQUE + catch), and
    # an empty name records none.
    org = uuid4()
    resolver = _resolver(db_session)

    await resolver.resolve_participant(org, "boyan@globex.com", display_name="Boyan")
    await resolver.resolve_participant(org, "boyan@globex.com", display_name="Boyan")  # dup
    await resolver.resolve_participant(org, "boyan@globex.com", display_name="Boyan Petrov")  # new
    await resolver.resolve_participant(org, "boyan@globex.com", display_name="")  # none

    assert await _count(db_session, PersonAlias, org) == 2  # "Boyan" + "Boyan Petrov"


async def test_resolve_recovers_from_concurrent_insert_race(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Exercise the race-safe recovery branch deterministically: pre-create the "winner" person+mail,
    # then stub ONLY the resolver's first lookup to miss so it takes the insert path and hits a REAL
    # UNIQUE(org_id,email) violation. The savepoint must roll back and the recovery re-read must
    # return the existing person — no duplicate, no crash, session still usable after.
    org = uuid4()
    resolver = _resolver(db_session)
    seed = PersonRepository(db_session)
    winner = await seed.insert(Person(org_id=org, display_name="Boyan"))
    await seed.add_email(
        PersonEmail(org_id=org, person_id=winner.id, email="boyan@globex.com", source="imap")
    )

    real_lookup = resolver._people.get_person_id_by_email
    calls = {"n": 0}

    async def lookup_miss_once(org_id: object, email: str) -> object:
        calls["n"] += 1
        return None if calls["n"] == 1 else await real_lookup(org_id, email)

    monkeypatch.setattr(resolver._people, "get_person_id_by_email", lookup_miss_once)

    resolved = await resolver.resolve_participant(org, "boyan@globex.com")

    assert resolved == winner.id  # recovery returned the concurrent winner
    assert await _count(db_session, Person, org) == 1  # no duplicate person
    assert await _count(db_session, Company, org) == 1  # session still usable post-rollback
