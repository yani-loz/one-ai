"""
Role: LIVE isolation tests for the generated-SQL hatch — the one executor that runs ARBITRARY
      model-written SQL over the same tables the six declarative tools read. Covers the two
      guarantees that only exist at runtime for this path: TENANT ISOLATION (testing.md's
      hardest rule) and the PER-PERSON rules that used to live only in hand-written tool SQL —
      BCC non-disclosure, the write-plane seen window, and the grant table.
Used by: pytest (tests/ask/tools). Requires a migrated + role-provisioned DB (through 0023);
      the ask_schema fixture skips loudly otherwise.
Depends on: app.ask.tools.sql_execution.execute_guarded_sql, app.core.database.reader_session,
      the tests/ask/conftest seed helpers.
Key invariants:
  - Every cross-tenant assertion is POSITIVE-CONTROLLED: org B's own reader session must see
    the row that org A cannot. Asserting only "org A sees 0" passes identically when RLS works
    and when the table is simply empty, and two such assertions in test_sql_execution.py were
    the reason this module exists (R5 quality audit).
  - These pin DATABASE rules, not tool code. Migration 0023 moved the BCC rule out of four
    Python queries and into a RESTRICTIVE policy precisely because the hatch, the
    counterparty_summary view and acl_grant were three planes that never had it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ask.exceptions import ToolExecutionError
from app.ask.tools.sql_execution import execute_guarded_sql
from app.connectors.imap.models.email import EmailMessage
from app.connectors.models.connector_connection import ConnectorConnection
from app.core.database import reader_session
from app.entities.models.person import Person

ConnectionSeeder = Callable[..., Awaitable[ConnectorConnection]]
PersonSeeder = Callable[..., Awaitable[Person]]
EmailSeeder = Callable[..., Awaitable[EmailMessage]]

_SENT_AT = datetime(2020, 1, 1, 12, 0, tzinfo=UTC)


async def _rows(org, person_id, sql: str) -> list[dict]:
    """Run one statement through the real hatch on a reader session."""
    async with reader_session(org, person_id) as session:
        _, rows = await execute_guarded_sql(session, sql, max_rows=50)
    return rows


# — Tenant isolation: the hatch reaches seven relations, so it needs its own negative ————


async def test_cross_tenant_generated_sql_counts_nothing_from_org_b(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    org_a, org_b = uuid4(), uuid4()
    reader_a = await seed_person(db_session, org_a, "Reader A", emails=("a@a.test",))
    connection_b = await seed_connection(db_session, org_b, mailbox="owner@b.test")
    person_b = await seed_person(db_session, org_b, "Person B", emails=("b@b.test",))
    await seed_email(
        db_session,
        org_b,
        connection_b.id,
        person_b.id,
        from_address="counterparty@b.test",
        subject="B settlement",
        sent_at=_SENT_AT,
    )
    await db_session.commit()
    statement = "SELECT count(*) AS n FROM email_message"

    seen_by_a = await _rows(org_a, reader_a.id, statement)
    seen_by_b = await _rows(org_b, person_b.id, statement)

    assert seen_by_a == [{"n": 0}]
    # The positive control: without it, this test passes just as loudly on an empty database.
    assert seen_by_b == [{"n": 1}]


async def test_cross_tenant_generated_sql_reads_no_org_b_subject(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    org_a, org_b = uuid4(), uuid4()
    reader_a = await seed_person(db_session, org_a, "Reader A", emails=("a@a.test",))
    connection_b = await seed_connection(db_session, org_b, mailbox="owner@b.test")
    person_b = await seed_person(db_session, org_b, "Person B", emails=("b@b.test",))
    await seed_email(
        db_session,
        org_b,
        connection_b.id,
        person_b.id,
        from_address="counterparty@b.test",
        subject="B merger terms",
        sent_at=_SENT_AT,
    )
    await db_session.commit()

    rows = await _rows(org_a, reader_a.id, "SELECT subject FROM email_message")

    assert rows == []
    assert "merger" not in str(rows)


# — Per-person visibility: the rules that used to live only in tool SQL ————————————————


async def test_generated_sql_never_returns_bcc_recipients(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # The R5 CRITICAL. The visibility policy admits every recipient row of a message the caller
    # can read, so before 0023 an ordinary "who else was on that email?" returned the blind
    # copies verbatim. The to/cc rows must still come back — a rule that hides everything is
    # not isolation, it is breakage.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "Reader", emails=("reader@a.test",))
    await seed_email(
        db_session,
        org,
        connection.id,
        reader.id,
        from_address="sender@a.test",
        subject="Board pack",
        sent_at=_SENT_AT,
        recipients=(
            ("to", "reader@a.test", "Reader"),
            ("cc", "colleague@a.test", "Colleague"),
            ("bcc", "secret@rival.test", "Blind Copy"),
        ),
    )
    await db_session.commit()

    rows = await _rows(org, reader.id, "SELECT kind, address FROM email_recipient")

    assert {row["address"] for row in rows} == {"reader@a.test", "colleague@a.test"}
    assert "secret@rival.test" not in str(rows)


async def test_generated_sql_cannot_count_bcc_rows_as_an_oracle(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # Existence leaks as readily as content: a count that includes the blind copy tells the
    # caller a hidden party exists without naming them.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "Reader", emails=("reader@a.test",))
    await seed_email(
        db_session,
        org,
        connection.id,
        reader.id,
        from_address="sender@a.test",
        subject="Board pack",
        sent_at=_SENT_AT,
        recipients=(
            ("to", "reader@a.test", "Reader"),
            ("bcc", "secret@rival.test", "Blind Copy"),
        ),
    )
    await db_session.commit()

    rows = await _rows(org, reader.id, "SELECT count(*) AS n FROM email_recipient")

    assert rows == [{"n": 1}]


async def test_generated_sql_sees_only_its_own_grants(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # acl_grant was admitted as "bookkeeping, never content". It carries one row per recipient
    # of every kind, so joining it to person/person_email rebuilds the blind-copied list BY
    # NAME without ever touching email_recipient — and enumerates a colleague's mailbox.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "Reader", emails=("reader@a.test",))
    colleague = await seed_person(db_session, org, "Colleague", emails=("colleague@a.test",))
    message = await seed_email(
        db_session,
        org,
        connection.id,
        reader.id,
        from_address="sender@a.test",
        subject="Board pack",
        sent_at=_SENT_AT,
    )
    # A grant belonging to somebody else, on the same message.
    from app.access.models.acl_grant import AclGrant

    db_session.add(
        AclGrant(
            org_id=org,
            person_id=colleague.id,
            object_type="email_message",
            object_id=message.id,
            connection_id=connection.id,
            provenance="recipient",
        )
    )
    await db_session.commit()

    rows = await _rows(org, reader.id, "SELECT person_id FROM acl_grant")

    assert [row["person_id"] for row in rows] == [reader.id]


async def test_generated_sql_cannot_read_the_write_plane_seen_window(
    db_session: AsyncSession,
    seed_person: PersonSeeder,
) -> None:
    # first_seen_at/last_seen_at are maintained over EVERY ingested message, including ones the
    # caller holds no grant for and including BCC-only contacts. find_person recomputes the
    # window from readable messages (V3/V4); the hatch served the raw columns straight past it.
    org = uuid4()
    reader = await seed_person(db_session, org, "Reader", emails=("reader@a.test",))
    await db_session.commit()

    with pytest.raises(ToolExecutionError):
        await _rows(org, reader.id, "SELECT first_seen_at FROM person")

    # The rest of the person row stays readable — the revoke is column-level, not table-level.
    rows = await _rows(org, reader.id, "SELECT display_name FROM person")
    assert {row["display_name"] for row in rows} == {"Reader"}
