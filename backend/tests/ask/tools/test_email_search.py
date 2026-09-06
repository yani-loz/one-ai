"""
Role: LIVE reader-plane tests for the message-search mechanisms — the correspondence envelope
      (sent_split + span_boundary_emails), NULL-safe ranking, per-party alias groups, and the
      completeness/absence contract of _search_emails. Exercises the REAL retrieval seam
      (reader_session): org RLS + the PF-01 `visibility` policy are both in force, so every
      assertion is on what a granted principal actually sees.
Used by: pytest (tests/ask/tools). Requires a migrated + role-provisioned DB (the reader role
      and the `visibility` policy are migration-only); the ask_schema fixture skips loudly.
Depends on: app.ask.tools.email_search (_search_emails), app.ask.tools.email_filters
      (_participants_all_groups), app.core.database.reader_session, tests/ask/conftest seeds.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ask.exceptions import ToolExecutionError
from app.ask.tools.email_filters import (
    _participant_terms,
    _participants_all_groups,
    _query_terms,
)
from app.ask.tools.email_search import _search_emails
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


# — Mechanism 1: the correspondence envelope (sent_split + span_boundary_emails) —


async def test_participants_all_search_reports_sent_split(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # 2 inbound + 1 outbound between two parties → the grouped direction split, not a row count.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "Reader")
    for day in (1, 2):
        await seed_email(
            db_session, org, connection.id, reader.id,
            from_address="bob@ext.test", direction="inbound", sent_at=_at(day),
            recipients=(("to", "alice@acme.test", None),),
        )
    await seed_email(
        db_session, org, connection.id, reader.id,
        from_address="alice@acme.test", direction="outbound", sent_at=_at(3),
        recipients=(("to", "bob@ext.test", None),),
    )
    await db_session.commit()

    async with reader_session(org, reader.id) as session:
        envelope = await _search_emails(session, {"participants_all": ["alice", "bob"]})

    assert envelope["total_matches"] == 3
    assert envelope["sent_split"] == {"inbound": 2, "outbound": 1, "unknown": 0}
    assert sum(envelope["sent_split"].values()) == envelope["total_matches"]
    # R4: the mailbox reference frame must be stated — sent_split is not who-wrote-to-whom.
    assert "SYNCED MAILBOX" in envelope["sent_split_note"]


async def test_per_term_keys_never_echo_a_caller_supplied_id(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # per_term_matches keys are the model's OWN search terms coming back in an observation.
    # A model that "searches" for an id it invented would otherwise read that id straight
    # back out as something a tool returned. The redaction at the call site had no test.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "Reader")
    await seed_email(
        db_session, org, connection.id, reader.id,
        from_address="bob@ext.test", subject="quarterly report", sent_at=_at(1),
    )
    await db_session.commit()
    invented = "44444444-4444-4444-4444-444444444444"

    async with reader_session(org, reader.id) as session:
        envelope = await _search_emails(session, {"queries": ["quarterly", invented]})

    assert "per_term_matches" in envelope
    assert invented not in str(envelope)


async def test_search_envelope_puts_control_fields_before_the_results_page(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # R2/N4's other half: field ORDER is a contract. The runner budgets the serialized
    # payload, so anything after `results` is what gets sacrificed first — the control fields
    # the tool description tells the model to rely on must all precede it.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "Reader")
    for day in (1, 2, 3):
        await seed_email(
            db_session, org, connection.id, reader.id,
            from_address="bob@ext.test", direction="inbound", sent_at=_at(day),
            recipients=(("to", "alice@acme.test", None),),
        )
    await db_session.commit()

    async with reader_session(org, reader.id) as session:
        envelope = await _search_emails(session, {"participants_all": ["alice", "bob"]})

    keys = list(envelope)
    assert keys[0] == "total_matches"
    assert keys[-1] == "results"
    for control in ("date_span", "sent_split", "listing_complete", "listing"):
        assert keys.index(control) < keys.index("results")


async def test_participant_search_reports_span_boundary_emails(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # 8 dated messages → exactly the 3 earliest + 3 latest, in order, first message on the edge.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "Reader")
    ids_by_day = {}
    for day in range(1, 9):
        message = await seed_email(
            db_session, org, connection.id, reader.id,
            from_address="bob@ext.test", direction="inbound", sent_at=_at(day),
            subject=f"day {day}", recipients=(("to", "alice@acme.test", None),),
        )
        ids_by_day[day] = message.id
    await db_session.commit()

    async with reader_session(org, reader.id) as session:
        envelope = await _search_emails(session, {"participants": ["alice"]})

    messages = envelope["span_boundary_emails"]["messages"]
    earliest = [m for m in messages if m["which"] == "earliest"]
    latest = [m for m in messages if m["which"] == "latest"]
    assert [m["id"] for m in earliest] == [ids_by_day[1], ids_by_day[2], ids_by_day[3]]
    assert [m["id"] for m in latest] == [ids_by_day[8], ids_by_day[7], ids_by_day[6]]
    assert earliest[0]["id"] == ids_by_day[1]  # the very first message is on the boundary


async def test_pure_keyword_search_has_no_correspondence_envelope(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # No participant filter → the correspondence envelope is not emitted even with total > 2.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "Reader")
    for day in (1, 2, 3):
        await seed_email(
            db_session, org, connection.id, reader.id,
            from_address="bob@ext.test", subject="quarterly report", sent_at=_at(day),
        )
    await db_session.commit()

    async with reader_session(org, reader.id) as session:
        envelope = await _search_emails(session, {"queries": ["quarterly"]})

    assert envelope["total_matches"] == 3
    assert "sent_split" not in envelope
    assert "span_boundary_emails" not in envelope


async def test_correspondence_envelope_absent_when_two_or_fewer_matches(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # Participant filter active but total <= 2 → too thin to be a 'correspondence arc'.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "Reader")
    for day in (1, 2):
        await seed_email(
            db_session, org, connection.id, reader.id,
            from_address="bob@ext.test", direction="inbound", sent_at=_at(day),
            recipients=(("to", "alice@acme.test", None),),
        )
    await db_session.commit()

    async with reader_session(org, reader.id) as session:
        envelope = await _search_emails(session, {"participants_all": ["alice", "bob"]})

    assert envelope["total_matches"] == 2
    assert "sent_split" not in envelope
    assert "span_boundary_emails" not in envelope


async def test_span_boundary_absent_in_small_band_but_sent_split_and_listing_present(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # 3–6 matches: the two LIMIT-3 boundary queries would overlap/duplicate and `listing`
    # already enumerates every match — so span_boundary is withheld while sent_split (a pure
    # aggregate) and the complete listing are still emitted.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "Reader")
    for day in range(1, 6):  # 5 matches → inside the 3–6 band
        await seed_email(
            db_session, org, connection.id, reader.id,
            from_address="bob@ext.test", direction="inbound", sent_at=_at(day),
            recipients=(("to", "alice@acme.test", None),),
        )
    await db_session.commit()

    async with reader_session(org, reader.id) as session:
        envelope = await _search_emails(session, {"participants": ["alice"]})

    assert envelope["total_matches"] == 5
    assert "span_boundary_emails" not in envelope
    assert "sent_split" in envelope
    assert envelope["listing_complete"] is True
    assert len(envelope["listing"]) == 5


async def test_span_boundary_has_no_duplicate_ids_above_six(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # total >= 7: the 3 earliest + 3 latest are disjoint (no message sits in both halves), so
    # the boundary carries exactly 6 distinct ids — the duplication the > 6 gate prevents.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "Reader")
    for day in range(1, 8):  # 7 matches → just above the gate
        await seed_email(
            db_session, org, connection.id, reader.id,
            from_address="bob@ext.test", direction="inbound", sent_at=_at(day),
            subject=f"day {day}", recipients=(("to", "alice@acme.test", None),),
        )
    await db_session.commit()

    async with reader_session(org, reader.id) as session:
        envelope = await _search_emails(session, {"participants": ["alice"]})

    assert envelope["total_matches"] == 7
    ids = [m["id"] for m in envelope["span_boundary_emails"]["messages"]]
    assert len(ids) == 6
    assert len(set(ids)) == 6  # no message appears in both the earliest and latest halves


async def test_span_boundary_tied_sent_at_still_six_distinct_ids(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # R5: 7 messages sharing ONE sent_at (a batch send) — without the inverted id
    # tie-breaker on the latest arm, both arms returned the SAME three ids.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "Reader")
    for i in range(7):
        await seed_email(
            db_session, org, connection.id, reader.id,
            from_address="bob@ext.test", direction="inbound", sent_at=_at(1),
            subject=f"batch {i}", recipients=(("to", "alice@acme.test", None),),
        )
    await db_session.commit()

    async with reader_session(org, reader.id) as session:
        envelope = await _search_emails(session, {"participants": ["alice"]})

    ids = [m["id"] for m in envelope["span_boundary_emails"]["messages"]]
    assert len(ids) == 6
    assert len(set(ids)) == 6  # earliest and latest halves are DISTINCT despite the tie


async def test_keyword_search_subject_hit_outranks_null_subject_rows(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # N3: subject is nullable and Postgres sorts NULLs FIRST under DESC — three newer
    # subject-less body matches must NOT push the true (older) subject hit off the top.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "Reader")
    hit = await seed_email(
        db_session, org, connection.id, reader.id,
        from_address="bob@ext.test", subject="Invoice 42", body_text="see attached",
        sent_at=_at(1),  # OLDEST — only the subject rank can put it first
    )
    for day in (5, 6, 7):
        await seed_email(
            db_session, org, connection.id, reader.id,
            from_address="bob@ext.test", subject=None,
            body_text="invoice details inside", sent_at=_at(day),
        )
    await db_session.commit()

    async with reader_session(org, reader.id) as session:
        envelope = await _search_emails(session, {"queries": ["invoice"]})

    assert envelope["total_matches"] == 4
    assert envelope["results"][0]["id"] == hit.id
    assert envelope["results"][0]["subject_hit"] is True
    # NULL subjects rank FALSE (not NULL) — never above real hits, and newest-first after.
    assert all(r["subject_hit"] is False for r in envelope["results"][1:])


async def test_participant_only_search_is_pure_recency_order(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # N3 (participant-only path): term_likes is the [''] placeholder — the rank expression
    # must be uniformly false, giving pure newest-first, instead of floating NULL subjects.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "Reader")
    oldest = await seed_email(
        db_session, org, connection.id, reader.id,
        from_address="bob@ext.test", subject="aaa", sent_at=_at(1),
        recipients=(("to", "alice@acme.test", None),),
    )
    middle_null_subject = await seed_email(
        db_session, org, connection.id, reader.id,
        from_address="bob@ext.test", subject=None, sent_at=_at(2),
        recipients=(("to", "alice@acme.test", None),),
    )
    newest = await seed_email(
        db_session, org, connection.id, reader.id,
        from_address="bob@ext.test", subject="ccc", sent_at=_at(3),
        recipients=(("to", "alice@acme.test", None),),
    )
    await db_session.commit()

    async with reader_session(org, reader.id) as session:
        envelope = await _search_emails(session, {"participants": ["alice"]})

    assert [r["id"] for r in envelope["results"]] == [
        newest.id, middle_null_subject.id, oldest.id
    ]


async def test_participants_all_alias_groups_match_any_alias_per_party(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # R1: (a_old OR a_new) AND (b_old OR b_new) — the shape a flat pattern list cannot
    # express. Every alias combination of the two parties matches; a third party does not.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "Reader")
    old_to_old = await seed_email(
        db_session, org, connection.id, reader.id,
        from_address="a-old@x.test", sent_at=_at(1),
        recipients=(("to", "b-old@y.test", None),),
    )
    new_to_new = await seed_email(
        db_session, org, connection.id, reader.id,
        from_address="a-new@x.test", sent_at=_at(2),
        recipients=(("to", "b-new@y.test", None),),
    )
    await seed_email(  # party A writes to an unrelated party C — must NOT match
        db_session, org, connection.id, reader.id,
        from_address="a-old@x.test", sent_at=_at(3),
        recipients=(("to", "c@z.test", None),),
    )
    await db_session.commit()

    async with reader_session(org, reader.id) as session:
        envelope = await _search_emails(session, {
            "participants_all": [
                ["a-old@x.test", "a-new@x.test"],
                ["b-old@y.test", "b-new@y.test"],
            ]
        })

    assert envelope["total_matches"] == 2
    assert {r["id"] for r in envelope["results"]} == {old_to_old.id, new_to_new.id}


async def test_search_zero_matches_carries_absence_warning_note(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # R1 amplifier: an over-restricted filter must never read as an authoritative empty
    # set — 0 matches carries an explicit "not verified absence" note.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "Reader")
    await seed_email(
        db_session, org, connection.id, reader.id,
        from_address="bob@ext.test", sent_at=_at(1),
        recipients=(("to", "alice@acme.test", None),),
    )
    await db_session.commit()

    async with reader_session(org, reader.id) as session:
        envelope = await _search_emails(
            session, {"participants_all": ["alice", "nobody-matches-this"]}
        )

    assert envelope["total_matches"] == 0
    assert "absence of matches" in envelope["listing_note"]


def test_participants_all_more_than_four_parties_raises() -> None:
    # R3/N14: an AND conjunct must never be silently dropped (dropping WIDENS the match).
    with pytest.raises(ToolExecutionError):
        _participants_all_groups(
            {"participants_all": ["alice", "bob", "carol", "dave", "erin"]}
        )


def test_participants_all_oversized_alias_group_raises() -> None:
    with pytest.raises(ToolExecutionError):
        _participants_all_groups({"participants_all": [[f"alias{i}@x" for i in range(9)]]})


def test_participants_all_flat_strings_stay_compatible() -> None:
    assert _participants_all_groups({"participants_all": ["alice", "bob"]}) == [
        ["alice"], ["bob"]
    ]


def test_participants_all_blank_party_entry_raises() -> None:
    # Skipping a blank entry drops an AND conjunct — the same silent WIDENING the cap check
    # exists to prevent, so it is an error rather than a skip.
    with pytest.raises(ToolExecutionError):
        _participants_all_groups({"participants_all": ["alice", "  "]})


def test_over_cap_query_variants_raise_instead_of_trimming() -> None:
    # An OR-variant dropped in silence NARROWS the match while total_matches keeps claiming
    # completeness — an under-counted answer the reader cannot detect.
    with pytest.raises(ToolExecutionError):
        _query_terms({"queries": [f"variant{i}" for i in range(6)]})


def test_over_cap_participants_raise_instead_of_trimming() -> None:
    with pytest.raises(ToolExecutionError):
        _participant_terms({"participants": [f"p{i}@x.test" for i in range(9)]})
