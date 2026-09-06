"""
Role: LIVE-DB tests for the counterparty_summary v3 view (migration 0022) — proves the volume
      columns count DISTINCT MESSAGES, not message×party CONTACT ROWS (finding F9 / M-36b), and
      that the fix holds on the REAL reader plane (security_invoker view + RLS + visibility).
      The view is a migration-only artifact (never in Base.metadata), so these tests require a
      migrated + role-provisioned DB and skip loudly otherwise (view-absent probe below).
Used by: pytest (tests/access). Seeds on the BYPASSRLS db_session, reads through reader_session —
      the same seam test_visibility_policies.py uses and the same seam get_counterparty_summary
      consumes in production.
Depends on: app.core.database.reader_session, the access conftest fixtures + seed helpers
      (seed_connection / seed_person / grant_email_access), the email Layer-1 ORM models.
Key invariants under test:
  - total_mentions / inbound_count / outbound_count = count(DISTINCT message_id) (v2 counted rows).
  - distinct_addresses is the v3-vs-v2 discriminator: it only reaches N when all N same-domain
    contact rows are actually visible, so pairing it with total_mentions==1 proves the collapse
    (3 rows visible AND folded into one message) rather than passing on a partially-visible row.
  - first/last_message_id remain the earliest/latest citable message ids (unchanged from v2).
  - Cross-tenant: a reader scoped to org A sees exactly org A's row/values — org B never bleeds in.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.imap.models.email import EmailMessage, EmailRecipient
from app.core.database import engine, reader_session
from tests.access.conftest import grant_email_access, seed_connection, seed_person

# Fixed, ordered timestamps so first/last_message_id are deterministic (T1 earliest, T2 latest).
_T1 = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
_T2 = datetime(2026, 3, 20, 15, 0, tzinfo=UTC)


@pytest_asyncio.fixture(autouse=True)
async def _require_counterparty_view() -> AsyncIterator[None]:
    """Skip the module unless the counterparty_summary view exists (it is migration-only)."""
    async with engine.connect() as connection:
        present = (
            await connection.execute(text("SELECT to_regclass('public.counterparty_summary')"))
        ).scalar() is not None
    if not present:
        pytest.skip("counterparty_summary view absent — run `alembic upgrade head` (>= 0020).")
    yield


async def _seed_counterparty_message(
    session: AsyncSession,
    org_id: UUID,
    connection_id: UUID,
    *,
    from_address: str,
    direction: str,
    sent_at: datetime,
    recipients: list[tuple[str, str]],
) -> EmailMessage:
    """Insert one restricted email (sender + given recipients) for the view to roll up.

    Born restricted/restricted (the DB pins email origin to 'restricted'); the caller grants a
    person access separately so the row becomes visible on the reader plane. `recipients` is a
    list of (kind, address) — kind in {'to','cc'}.
    """
    message = EmailMessage(
        org_id=org_id,
        connection_id=connection_id,
        visibility_scope="restricted",
        origin_scope="restricted",
        container_id=connection_id,
        dedup_key=f"cp-{uuid4()}",
        from_address=from_address,
        direction=direction,
        sent_at=sent_at,
        subject="counterparty seed",
        headers={},
    )
    session.add(message)
    await session.flush()
    for kind, address in recipients:
        session.add(
            EmailRecipient(
                org_id=org_id,
                email_id=message.id,
                visibility_scope="restricted",
                origin_scope="restricted",
                container_id=connection_id,
                kind=kind,
                address=address,
            )
        )
    await session.flush()
    return message


async def _summary_rows(
    org_id: UUID, person_id: UUID | None, domain: str
) -> list[dict[str, object]]:
    """Read counterparty_summary for one domain through the REAL person-scoped reader seam."""
    async with reader_session(org_id, person_id) as session:
        result = await session.execute(
            text(
                """
                SELECT org_id, domain, inbound_count, outbound_count, total_mentions,
                       distinct_addresses, first_message_id, last_message_id
                FROM counterparty_summary
                WHERE domain = :domain
                """
            ),
            {"domain": domain},
        )
        return [dict(r) for r in result.mappings()]


async def test_volume_columns_count_distinct_messages_not_contact_rows(
    db_session: AsyncSession,
) -> None:
    # F9/M-36b: ONE inbound message from external.com CC'ing two more external.com addresses is
    # THREE contact rows for the domain — v2 said total_mentions=3; v3 must say 1 (distinct msg).
    org = uuid4()
    connection = await seed_connection(db_session, org)
    person = await seed_person(db_session, org)
    message = await _seed_counterparty_message(
        db_session,
        org,
        connection.id,
        from_address="a@external.com",
        direction="inbound",
        sent_at=_T1,
        recipients=[
            ("to", "b@external.com"),
            ("cc", "c@external.com"),
            ("to", "insider@acme.test"),  # a different domain — ignored by the external.com filter
        ],
    )
    await grant_email_access(db_session, org, person.id, message.id, connection.id)
    await db_session.commit()

    rows = await _summary_rows(org, person.id, "external.com")

    assert len(rows) == 1
    row = rows[0]
    assert row["total_mentions"] == 1  # v2 would report 3 (a@ sender + b@ + c@ recipients)
    assert row["inbound_count"] == 1
    assert row["outbound_count"] == 0
    # The discriminator: all THREE external.com contact rows are visible (distinct_addresses=3)
    # AND collapse to ONE message — a partial-visibility pass could not show both.
    assert row["distinct_addresses"] == 3


async def test_direction_split_counts_distinct_messages_each_way(
    db_session: AsyncSession,
) -> None:
    # Adding an OUTBOUND message to the same domain: inbound=1, outbound=1, total=2 distinct msgs.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    person = await seed_person(db_session, org)
    inbound = await _seed_counterparty_message(
        db_session,
        org,
        connection.id,
        from_address="a@external.com",
        direction="inbound",
        sent_at=_T1,
        recipients=[("to", "b@external.com"), ("cc", "c@external.com")],
    )
    outbound = await _seed_counterparty_message(
        db_session,
        org,
        connection.id,
        from_address="insider@acme.test",
        direction="outbound",
        sent_at=_T2,
        recipients=[("to", "d@external.com")],
    )
    await grant_email_access(db_session, org, person.id, inbound.id, connection.id)
    await grant_email_access(db_session, org, person.id, outbound.id, connection.id)
    await db_session.commit()

    rows = await _summary_rows(org, person.id, "external.com")

    assert len(rows) == 1
    row = rows[0]
    assert row["inbound_count"] == 1
    assert row["outbound_count"] == 1
    assert row["total_mentions"] == 2
    assert row["distinct_addresses"] == 4  # a@, b@, c@ (inbound) + d@ (outbound)


async def test_first_and_last_message_id_point_at_earliest_and_latest(
    db_session: AsyncSession,
) -> None:
    # The citable-id columns are unchanged from v2: earliest/latest message the domain appears on.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    person = await seed_person(db_session, org)
    earliest = await _seed_counterparty_message(
        db_session,
        org,
        connection.id,
        from_address="a@external.com",
        direction="inbound",
        sent_at=_T1,
        recipients=[("to", "b@external.com")],
    )
    latest = await _seed_counterparty_message(
        db_session,
        org,
        connection.id,
        from_address="insider@acme.test",
        direction="outbound",
        sent_at=_T2,
        recipients=[("to", "d@external.com")],
    )
    await grant_email_access(db_session, org, person.id, earliest.id, connection.id)
    await grant_email_access(db_session, org, person.id, latest.id, connection.id)
    await db_session.commit()

    rows = await _summary_rows(org, person.id, "external.com")

    assert len(rows) == 1
    row = rows[0]
    assert row["first_message_id"] == earliest.id
    assert row["last_message_id"] == latest.id


async def test_cross_tenant_reader_sees_only_own_org_counterparty(
    db_session: AsyncSession,
) -> None:
    # NON-NEGOTIABLE: org B's external.com traffic (a heavier 3-message volume) must NOT bleed into
    # org A's reader view — org A sees exactly its own single row, and a B-only domain is invisible.
    org_a, org_b = uuid4(), uuid4()
    connection_a = await seed_connection(db_session, org_a, mailbox="a@acme.test")
    connection_b = await seed_connection(db_session, org_b, mailbox="b@beta.test")
    person_a = await seed_person(db_session, org_a, "Org A Person")

    org_a_message = await _seed_counterparty_message(
        db_session,
        org_a,
        connection_a.id,
        from_address="a@external.com",
        direction="inbound",
        sent_at=_T1,
        recipients=[("to", "insider@acme.test")],
    )
    await grant_email_access(db_session, org_a, person_a.id, org_a_message.id, connection_a.id)

    # Org B: three DISTINCT inbound external.com messages (volume 3 ≠ org A's 1) + a B-only domain.
    for sender in ("x@external.com", "y@external.com", "z@external.com"):
        await _seed_counterparty_message(
            db_session,
            org_b,
            connection_b.id,
            from_address=sender,
            direction="inbound",
            sent_at=_T1,
            recipients=[("to", "insider@beta.test")],
        )
    await _seed_counterparty_message(
        db_session,
        org_b,
        connection_b.id,
        from_address="q@onlyb.example",
        direction="inbound",
        sent_at=_T2,
        recipients=[("to", "insider@beta.test")],
    )
    await db_session.commit()

    external_rows = await _summary_rows(org_a, person_a.id, "external.com")
    onlyb_rows = await _summary_rows(org_a, person_a.id, "onlyb.example")

    assert len(external_rows) == 1
    row = external_rows[0]
    assert row["org_id"] == org_a  # never org B's row
    assert row["total_mentions"] == 1  # org A's single message — NOT summed with org B's 3
    assert row["inbound_count"] == 1
    assert onlyb_rows == []  # a domain that exists only in org B is invisible to org A's reader
