"""
Role: Integration tests for the per-run ResolutionCache wired into EntityResolver (perf lane #2) —
      repeat sightings skip repository round-trips, staged entries confirm on commit, and (the
      correctness core) a per-email ROLLBACK discards staged ids so a person minted in a failed
      email transaction can never poison later emails with a dangling person_id.
Used by: pytest (tests/entities). Real DB via the entities conftest (entity_schema + db_session).
Depends on: app.entities.services.entity_resolver + .resolution_cache + the entity repositories.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.models.person import Person, PersonAlias, PersonEmail
from app.entities.repositories.company_repository import CompanyRepository
from app.entities.repositories.person_repository import PersonRepository
from app.entities.services.entity_resolver import EntityResolver
from tests.conftest import seed_org

MAILBOX = "owner@acme.com"


def _resolver(session: AsyncSession) -> EntityResolver:
    return EntityResolver(session, mailbox_address=MAILBOX, source="imap")


def _count_calls(monkeypatch: pytest.MonkeyPatch, owner: type, method_name: str) -> list[int]:
    """Wrap `owner.method_name` with a delegating call counter; returns the mutable count cell."""
    calls = [0]
    original = getattr(owner, method_name)

    async def _counted(self: object, *args: object, **kwargs: object) -> object:
        calls[0] += 1
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(owner, method_name, _counted)
    return calls


async def test_repeat_sighting_resolves_person_via_cache_not_repository(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await seed_org()
    resolver = _resolver(db_session)
    lookups = _count_calls(monkeypatch, PersonRepository, "get_person_id_by_email")

    first = await resolver.resolve_participant(org, "boyan@globex.com")
    second = await resolver.resolve_participant(org, "boyan@globex.com")

    assert first == second
    assert lookups[0] == 1  # the second sighting never re-queried the match key


async def test_rollback_discards_staged_ids_and_re_resolves_cleanly(
    db_session: AsyncSession,
) -> None:
    # The correctness core: a person minted inside a rolled-back (failed-email) transaction must
    # NOT survive in the cache — a stale id would FK-poison every later email of the same sender.
    org = await seed_org()
    resolver = _resolver(db_session)

    stale = await resolver.resolve_participant(org, "boyan@globex.com")
    await db_session.rollback()  # the email's transaction failed — the person row is gone

    fresh = await resolver.resolve_participant(org, "boyan@globex.com")
    await db_session.commit()

    assert stale != fresh  # the discarded id was never served again
    row = (
        await db_session.execute(
            select(PersonEmail.person_id).where(
                PersonEmail.org_id == org, PersonEmail.email == "boyan@globex.com"
            )
        )
    ).scalar_one()
    assert row == fresh  # the re-resolution actually created the durable row


async def test_savepoint_rollback_discards_only_entries_staged_inside_it(
    db_session: AsyncSession,
) -> None:
    # 2026-07-04 review P1: a caller may wrap one email's work in its OWN begin_nested(); ids
    # staged inside a rolled-back savepoint must die with it, while entries staged before it
    # survive to commit — depth-tagged staging, not all-or-nothing.
    org = await seed_org()
    resolver = _resolver(db_session)
    before = await resolver.resolve_participant(org, "before@globex.com")

    stale_inside = None
    try:
        async with db_session.begin_nested():
            stale_inside = await resolver.resolve_participant(org, "inside@globex.com")
            raise RuntimeError("the email failed mid-savepoint")
    except RuntimeError:
        pass

    fresh_inside = await resolver.resolve_participant(org, "inside@globex.com")
    await db_session.commit()

    assert fresh_inside != stale_inside  # the rolled-back id was never served again
    rows = {
        email: person_id
        for email, person_id in (
            await db_session.execute(
                select(PersonEmail.email, PersonEmail.person_id).where(PersonEmail.org_id == org)
            )
        ).all()
    }
    assert rows["before@globex.com"] == before  # staged before the savepoint → survived
    assert rows["inside@globex.com"] == fresh_inside  # re-resolution created the durable row


async def test_commit_promotes_cache_so_later_emails_skip_the_lookup(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await seed_org()
    resolver = _resolver(db_session)
    first = await resolver.resolve_participant(org, "boyan@globex.com")
    await db_session.commit()  # per-email commit — staged entries become confirmed

    async def _forbidden(*_: object, **__: object) -> None:
        raise AssertionError("repository lookup ran despite a confirmed cache entry")

    monkeypatch.setattr(PersonRepository, "get_person_id_by_email", _forbidden)
    second = await resolver.resolve_participant(org, "boyan@globex.com")

    assert first == second


async def test_repeat_sighting_writes_alias_and_company_link_once(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Before the cache these two savepoint INSERTs ran (and deliberately failed on the UNIQUE)
    # once per participant per email — the measured resolver hot spot.
    org = await seed_org()
    resolver = _resolver(db_session)
    alias_writes = _count_calls(monkeypatch, PersonRepository, "add_alias")
    link_writes = _count_calls(monkeypatch, CompanyRepository, "link_person")

    for _ in range(3):
        await resolver.resolve_participant(org, "boyan@globex.com", display_name="Boyan")

    assert alias_writes[0] == 1
    assert link_writes[0] == 1
    alias_count = (
        await db_session.execute(
            select(func.count()).select_from(PersonAlias).where(PersonAlias.org_id == org)
        )
    ).scalar_one()
    assert alias_count == 1


async def test_seen_window_update_skipped_when_covered_and_issued_when_widening(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await seed_org()
    resolver = _resolver(db_session)
    window_updates = _count_calls(monkeypatch, PersonRepository, "extend_seen_window")
    inside = datetime(2026, 3, 15, tzinfo=UTC)
    earlier = datetime(2026, 1, 1, tzinfo=UTC)

    await resolver.resolve_participant(org, "boyan@globex.com", seen_at=inside)
    await resolver.resolve_participant(org, "boyan@globex.com", seen_at=inside)  # covered → skip
    await resolver.resolve_participant(org, "boyan@globex.com", seen_at=earlier)  # widens → UPDATE

    assert window_updates[0] == 2
    person = (await db_session.execute(select(Person).where(Person.org_id == org))).scalar_one()
    assert person.first_seen_at == earlier
    assert person.last_seen_at == inside
