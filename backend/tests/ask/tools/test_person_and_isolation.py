"""
Role: LIVE reader-plane tests for identity lookup (_find_person domain-migration candidates)
      and the ISOLATION guarantees every Ask tool inherits — cross-tenant blindness and
      within-org person visibility. Exercises the REAL retrieval seam (reader_session), so a
      leak in the policies fails here rather than in production.
Used by: pytest (tests/ask/tools). Requires a migrated + role-provisioned DB (the reader role
      and the `visibility` policy are migration-only); the ask_schema fixture skips loudly.
Depends on: app.ask.tools.person_tool (_find_person), app.ask.tools.email_search
      (_search_emails), app.core.database.reader_session, tests/ask/conftest seeds.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.ask.tools.email_search import _search_emails
from app.ask.tools.person_tool import _find_person
from app.connectors.imap.models.email import EmailMessage
from app.connectors.models.connector_connection import ConnectorConnection
from app.core.database import reader_session
from app.entities.models.person import Person

# Seed helpers arrive as factory fixtures (tests/ask/conftest.py) — injected by name, never
# imported — so no test module imports another test module (testing.md fixtures-over-imports rule).
ConnectionSeeder = Callable[..., Awaitable[ConnectorConnection]]
PersonSeeder = Callable[..., Awaitable[Person]]
EmailSeeder = Callable[..., Awaitable[EmailMessage]]


def _at(day: int) -> datetime:
    """A distinct, timezone-aware sent_at inside one fixed month (order-only significance)."""
    return datetime(2020, 1, day, 12, 0, tzinfo=UTC)



async def test_find_person_contact_window_never_exceeds_what_the_caller_can_read(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # person.first_seen_at/last_seen_at are maintained on the WRITE plane over every ingested
    # message, and `person` carries org isolation only — so serving them told a colleague the
    # DATE of correspondence they hold no grant for, while search_emails in the same session
    # certified that message does not exist. The window must come from what THIS caller sees.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    alice = await seed_person(db_session, org, "Alice", emails=("alice@acme.test",))
    bob = await seed_person(db_session, org, "Bob", emails=("bob@acme.test",))
    partner = "partner@shared.test"
    await seed_person(db_session, org, "Shared Partner", emails=(partner,))
    # Alice may read the two early messages; only Bob may read the later one.
    for day in (2, 3):
        await seed_email(
            db_session, org, connection.id, alice.id,
            from_address=partner, subject="early thread", sent_at=_at(day),
        )
    await seed_email(
        db_session, org, connection.id, bob.id,
        from_address=partner, subject="later side letter", sent_at=_at(20),
    )
    await db_session.commit()

    async with reader_session(org, alice.id) as session:
        found = await _find_person(session, {"name_or_email": "Shared Partner"})
        envelope = await _search_emails(session, {"participants": [partner]})

    person = found["persons"][0]
    assert envelope["total_matches"] == 2  # Alice sees only her own grants
    latest_visible = str(envelope["date_span"]["latest"]["sent_at"])[:10]
    assert str(person["last_seen"]) == latest_visible
    assert str(_at(20).date()) not in str(person)  # Bob's later date never appears


async def test_find_person_window_ignores_blind_copies(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # A contact WINDOW computed over blind-copy rows is an oracle: first_seen/last_seen would
    # shift for an address that only ever appears as a BCC, answering the question BCC exists
    # to hide. The person here is BCC-only, so a correct window is empty.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "Reader", emails=("reader@acme.test",))
    watcher = "secret-watcher@legal.test"
    await seed_person(db_session, org, "Legal Watcher", emails=(watcher,))
    await seed_email(
        db_session, org, connection.id, reader.id,
        from_address="sender@acme.test", subject="Deal terms", sent_at=_at(7),
        recipients=(("to", "reader@acme.test", None), ("bcc", watcher, "Legal Watcher")),
    )
    await db_session.commit()

    async with reader_session(org, reader.id) as session:
        found = await _find_person(session, {"name_or_email": "Legal Watcher"})

    person = found["persons"][0]
    assert person["first_seen"] is None
    assert person["last_seen"] is None
    assert str(_at(7).date()) not in str(person)


async def test_participants_all_cannot_be_used_as_a_bcc_oracle(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # The AND-filter (per-party alias groups) needs the same BCC rule as the OR-filter: a
    # "correspondence between A and B" query that MATCHES blind-copy rows answers "were these
    # two ever on the same message?" for a recipient who was deliberately hidden.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "Reader", emails=("reader@acme.test",))
    watcher = "secret-watcher@legal.test"
    await seed_email(
        db_session, org, connection.id, reader.id,
        from_address="sender@acme.test", subject="Deal terms", sent_at=_at(7),
        recipients=(("to", "reader@acme.test", None), ("bcc", watcher, "Legal Watcher")),
    )
    await db_session.commit()

    async with reader_session(org, reader.id) as session:
        blind = await _search_emails(
            session, {"participants_all": ["sender@acme.test", watcher]}
        )
        visible = await _search_emails(
            session, {"participants_all": ["sender@acme.test", "reader@acme.test"]}
        )

    assert blind["total_matches"] == 0  # the blind copy is not a discoverable participant
    assert visible["total_matches"] == 1  # …while ordinary addressing still works


async def test_find_person_surfaces_domain_migration_candidate(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # Same local-part + same full display name on another domain = the same person, surfaced.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    jane = await seed_person(db_session, org, "Jane Doe", emails=("j.doe@a.com",))
    await seed_email(
        db_session, org, connection.id, jane.id,
        from_address="j.doe@b.com", from_name="Jane Doe", sent_at=_at(1),
    )
    await db_session.commit()

    async with reader_session(org, jane.id) as session:
        result = await _find_person(session, {"name_or_email": "Jane"})

    person = result["persons"][0]
    assert person["same_person_candidates"] == ["j.doe@b.com"]
    assert "identity_note" in result


async def test_find_person_excludes_shared_token_short_and_linked(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # Three negatives excluded by LOGIC while the mechanism is provably live: one VALID candidate
    # is seeded alongside them (same seed+grant path), so an empty result would be a real bug,
    # not mere invisibility. The three excluded rows are visible to the reader — just not matched.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    jane = await seed_person(
        db_session, org, "Jane Doe", emails=("j.doe@a.com", "jd@a.com")
    )
    # VALID control: same local-part + same full name on a new domain → this one MUST surface.
    await seed_email(
        db_session, org, connection.id, jane.id,
        from_address="j.doe@valid.com", from_name="Jane Doe", sent_at=_at(1),
    )
    # (i) shared surname token but a DIFFERENT full name.
    await seed_email(
        db_session, org, connection.id, jane.id,
        from_address="j.doe@c.com", from_name="John Doe", sent_at=_at(2),
    )
    # (ii) local-part 'jd' (len < 4) — would match the linked jd@a.com but the guard drops it.
    await seed_email(
        db_session, org, connection.id, jane.id,
        from_address="jd@b.com", from_name="Jane Doe", sent_at=_at(3),
    )
    # (iii) the already-linked address itself must never be offered as a NEW identity.
    await seed_email(
        db_session, org, connection.id, jane.id,
        from_address="j.doe@a.com", from_name="Jane Doe", sent_at=_at(4),
    )
    await db_session.commit()

    async with reader_session(org, jane.id) as session:
        result = await _find_person(session, {"name_or_email": "Jane"})

    person = result["persons"][0]
    assert person["same_person_candidates"] == ["j.doe@valid.com"]  # only the valid one
    assert "j.doe@c.com" not in person["same_person_candidates"]  # different full name
    assert "jd@b.com" not in person["same_person_candidates"]  # len<4 local-part
    assert "j.doe@a.com" not in person["same_person_candidates"]  # already linked


# — The non-negotiable: cross-tenant isolation on both mechanisms —


async def test_cross_tenant_search_never_sees_org_b_traffic(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    org_a, org_b = uuid4(), uuid4()
    conn_a = await seed_connection(db_session, org_a, mailbox="a@acme.test")
    conn_b = await seed_connection(db_session, org_b, mailbox="b@beta.test")
    reader_a = await seed_person(db_session, org_a, "Reader A")
    reader_b = await seed_person(db_session, org_b, "Reader B")
    a_ids, b_ids = set(), set()
    for day in range(1, 8):  # 7 per org → total > 6 so span_boundary is emitted and tested
        a = await seed_email(
            db_session, org_a, conn_a.id, reader_a.id,
            from_address="bob@ext.test", direction="inbound", sent_at=_at(day),
            recipients=(("to", "alice@acme.test", None),),
        )
        b = await seed_email(
            db_session, org_b, conn_b.id, reader_b.id,
            from_address="bob@ext.test", direction="inbound", sent_at=_at(day),
            recipients=(("to", "alice@acme.test", None),),
        )
        a_ids.add(a.id)
        b_ids.add(b.id)
    await db_session.commit()

    async with reader_session(org_a, reader_a.id) as session:
        envelope = await _search_emails(session, {"participants_all": ["alice", "bob"]})

    assert envelope["total_matches"] == 7
    result_ids = {row["id"] for row in envelope["results"]}
    assert result_ids <= a_ids
    assert result_ids.isdisjoint(b_ids)
    boundary_ids = {m["id"] for m in envelope["span_boundary_emails"]["messages"]}
    assert boundary_ids.isdisjoint(b_ids)


async def test_cross_tenant_find_person_never_sees_org_b_candidate(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    org_a, org_b = uuid4(), uuid4()
    conn_a = await seed_connection(db_session, org_a, mailbox="a@acme.test")
    conn_b = await seed_connection(db_session, org_b, mailbox="b@beta.test")
    jane_a = await seed_person(db_session, org_a, "Jane Doe", emails=("j.doe@a.com",))
    jane_b = await seed_person(db_session, org_b, "Jane Doe", emails=("j.doe@a.com",))
    await seed_email(
        db_session, org_a, conn_a.id, jane_a.id,
        from_address="j.doe@aaa.com", from_name="Jane Doe", sent_at=_at(1),
    )
    await seed_email(
        db_session, org_b, conn_b.id, jane_b.id,
        from_address="j.doe@bbb.com", from_name="Jane Doe", sent_at=_at(1),
    )
    await db_session.commit()

    async with reader_session(org_a, jane_a.id) as session:
        result = await _find_person(session, {"name_or_email": "Jane"})

    person = result["persons"][0]
    assert person["same_person_candidates"] == ["j.doe@aaa.com"]


# — Within-org person visibility: a grant on one message does not leak an ungranted sibling —


async def test_person_visibility_hides_ungranted_message_within_org(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # Same org, same participant filter: the reader is granted 7 messages but NOT an 8th (granted
    # only to a decoy person). The PF-01 visibility policy gates per-principal, so the ungranted
    # message must appear in NO field of the envelope — results, date_span, span_boundary, listing
    # — and it must not even be counted. Making it the LATEST message gives date_span/boundary
    # real teeth: a leak would surface it as the newest.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "Reader")
    decoy = await seed_person(db_session, org, "Decoy")
    granted_latest_id = None
    for day in range(1, 8):  # 7 granted → total > 6, so span_boundary is emitted too
        message = await seed_email(
            db_session, org, connection.id, reader.id,
            from_address="bob@ext.test", direction="inbound", sent_at=_at(day),
            subject=f"day {day}", recipients=(("to", "alice@acme.test", None),),
        )
        granted_latest_id = message.id  # day 7, the newest message the reader CAN see
    # The 8th (latest of all) is granted ONLY to the decoy — the reader holds no grant to it.
    ungranted = await seed_email(
        db_session, org, connection.id, decoy.id,
        from_address="bob@ext.test", direction="inbound", sent_at=_at(8),
        subject="day 8", recipients=(("to", "alice@acme.test", None),),
    )
    await db_session.commit()

    async with reader_session(org, reader.id) as session:
        envelope = await _search_emails(session, {"participants": ["alice"]})

    ids_in_envelope = {row["id"] for row in envelope["results"]}
    ids_in_envelope.update(edge["id"] for edge in envelope["date_span"].values())
    ids_in_envelope.update(m["id"] for m in envelope["span_boundary_emails"]["messages"])
    listing_blob = " ".join(envelope["listing"])

    assert envelope["total_matches"] == 7  # the ungranted message is invisible, not counted
    assert ungranted.id not in ids_in_envelope
    assert str(ungranted.id) not in listing_blob
    assert granted_latest_id in ids_in_envelope  # a granted message IS surfaced
    assert str(granted_latest_id) in listing_blob
