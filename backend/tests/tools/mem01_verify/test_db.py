"""
Role: Seals the database planes of contract R6 / §1.3 `db` — the corpus snapshot session is
      REPEATABLE READ and READ ONLY at the server on every transaction (a write is refused by the
      server with SQLSTATE 25006), sees one snapshot while another connection commits, is
      tenant-scoped and bound to the requested database; the probe session factories expose the
      three REAL application planes (write / reader with RLS / global) bound to the probe.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.db (imported inside each test); the session probe corpus;
      sqlalchemy.text for raw statements.
Key invariants:
  - Every "sees zero" or "is refused" assertion pairs with a positive control on the same plane.
  - Only the probe database is ever opened.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.tools.mem01_verify import seeding
from tests.tools.mem01_verify.conftest import (
    SESSION_LOOP,
    DevServer,
    InstrumentLoader,
    ProbeCorpusFactory,
)

READ_ONLY_SQLSTATE = "25006"


async def _scalar(conn: object, sql: str, **params: object) -> object:
    result = await conn.execute(text(sql), params)  # type: ignore[attr-defined]
    return result.scalar_one()


def _sqlstate(error: DBAPIError) -> str | None:
    original = error.orig
    return getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)


@SESSION_LOOP
async def test_snapshot_session_is_repeatable_read_and_read_only_on_every_transaction(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory
) -> None:
    corpus = await probe_corpus()
    db = instrument("db")

    async with db.readonly_corpus_snapshot(corpus.small.org_id, database=corpus.database) as conn:
        first = (
            await _scalar(conn, "SHOW transaction_isolation"),
            await _scalar(conn, "SHOW transaction_read_only"),
        )
        await conn.commit()
        after_commit = (
            await _scalar(conn, "SHOW transaction_isolation"),
            await _scalar(conn, "SHOW transaction_read_only"),
        )
        await conn.rollback()
        after_rollback = (
            await _scalar(conn, "SHOW transaction_isolation"),
            await _scalar(conn, "SHOW transaction_read_only"),
        )

    assert first == ("repeatable read", "on")
    assert after_commit == ("repeatable read", "on")
    assert after_rollback == ("repeatable read", "on")


@SESSION_LOOP
async def test_snapshot_session_write_is_refused_by_the_server(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory
) -> None:
    corpus = await probe_corpus()
    db = instrument("db")
    org_id = corpus.small.org_id

    async with db.readonly_corpus_snapshot(org_id, database=corpus.database) as conn:
        assert await _scalar(conn, "SELECT 1") == 1  # positive control: reads work
        with pytest.raises(DBAPIError) as temp_table:
            await conn.execute(text("CREATE TEMP TABLE oracle_ro_probe (x int)"))
        await conn.rollback()
        with pytest.raises(DBAPIError) as update:
            await conn.execute(
                text("UPDATE email_message SET subject = subject WHERE org_id = :org"),
                {"org": str(org_id)},
            )
        await conn.rollback()
        still_readable = await _scalar(conn, "SELECT count(*) FROM email_message")

    assert _sqlstate(temp_table.value) == READ_ONLY_SQLSTATE
    assert _sqlstate(update.value) == READ_ONLY_SQLSTATE
    assert still_readable == corpus.small.email_count


@SESSION_LOOP
async def test_snapshot_session_is_tenant_scoped_and_bound_to_the_requested_database(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory
) -> None:
    corpus = await probe_corpus()
    db = instrument("db")

    async with db.readonly_corpus_snapshot(corpus.small.org_id, database=corpus.database) as conn:
        small_count = await _scalar(conn, "SELECT count(*) FROM email_message")
        database = await _scalar(conn, "SELECT current_database()")
    async with db.readonly_corpus_snapshot(corpus.big.org_id, database=corpus.database) as conn:
        big_count = await _scalar(conn, "SELECT count(*) FROM email_message")

    assert small_count == corpus.small.email_count
    assert big_count == corpus.big.email_count
    assert database == corpus.database


@SESSION_LOOP
async def test_snapshot_session_keeps_one_snapshot_while_another_connection_commits(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory
) -> None:
    corpus = await probe_corpus()
    db = instrument("db")
    iso = corpus.iso
    sessions = db.probe_session_factories(corpus.database)

    async with db.readonly_corpus_snapshot(iso.org_id, database=corpus.database) as snapshot:
        await seeding.assert_probe_connection(snapshot)
        before = await _scalar(snapshot, "SELECT count(*) FROM email_message")
        async with sessions.global_() as writer:
            await seeding.add_iso_email(writer, iso, 100)
            await writer.commit()
        inside = await _scalar(snapshot, "SELECT count(*) FROM email_message")
    async with db.readonly_corpus_snapshot(iso.org_id, database=corpus.database) as fresh:
        after = await _scalar(fresh, "SELECT count(*) FROM email_message")

    assert inside == before
    assert after == before + 1  # positive control: the commit really happened


@SESSION_LOOP
async def test_probe_session_factories_bind_the_three_planes_to_the_probe(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory, dev_server: DevServer
) -> None:
    from app.core.config import get_settings

    corpus = await probe_corpus()
    db = instrument("db")
    settings = get_settings()
    sessions = db.probe_session_factories(corpus.database)
    org_id = corpus.small.org_id

    assert sessions.database == corpus.database
    async with sessions.write(org_id) as write:
        write_identity = (
            await _scalar(write, "SELECT current_user"),
            await _scalar(write, "SELECT current_database()"),
        )
    async with sessions.reader(org_id, None) as reader:
        reader_identity = (
            await _scalar(reader, "SELECT current_user"),
            await _scalar(reader, "SELECT current_database()"),
        )
    async with sessions.global_() as global_plane:
        global_identity = (
            await _scalar(global_plane, "SELECT current_user"),
            await _scalar(global_plane, "SELECT current_database()"),
        )

    assert write_identity == (settings.app_db_user, corpus.database)
    assert reader_identity == (settings.reader_db_user, corpus.database)
    assert global_identity == (settings.global_db_user, corpus.database)
    assert dev_server.port == settings.postgres_port


@SESSION_LOOP
async def test_reader_plane_enforces_person_visibility_and_write_plane_does_not(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory
) -> None:
    corpus = await probe_corpus()
    db = instrument("db")
    small = corpus.small
    sessions = db.probe_session_factories(corpus.database)
    granted_person, ungranted_person = small.person_ids[0], small.person_ids[2]
    count_sql = "SELECT count(*) FROM email_message"

    async with sessions.reader(small.org_id, granted_person) as reader:
        granted_sees = await _scalar(reader, count_sql)
    async with sessions.reader(small.org_id, ungranted_person) as reader:
        ungranted_sees = await _scalar(reader, count_sql)
    async with sessions.reader(small.org_id, None) as reader:
        no_person_sees = await _scalar(reader, count_sql)
    async with sessions.reader(corpus.big.org_id, granted_person) as reader:
        cross_org_sees = await _scalar(reader, count_sql)
    async with sessions.write(small.org_id) as write:
        write_sees = await _scalar(write, count_sql)

    assert granted_sees == small.grant_count  # the positive control (3 granted rows)
    assert ungranted_sees == 0 and no_person_sees == 0 and cross_org_sees == 0
    assert write_sees == small.email_count


@SESSION_LOOP
async def test_reader_plane_never_discloses_bcc_rows_to_a_non_bcc_persona(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory
) -> None:
    corpus = await probe_corpus()
    db = instrument("db")
    small = corpus.small
    sessions = db.probe_session_factories(corpus.database)
    first_email: UUID = small.email_ids[0]

    async with sessions.reader(small.org_id, small.person_ids[0]) as reader:
        await seeding.assert_probe_connection(reader)
        kinds = await reader.execute(
            text("SELECT kind FROM email_recipient WHERE email_id = :email ORDER BY kind"),
            {"email": str(first_email)},
        )
        visible_kinds = [row[0] for row in kinds]
    async with sessions.write(small.org_id) as write:
        await seeding.assert_probe_connection(write)
        all_kinds = await write.execute(
            text("SELECT kind FROM email_recipient WHERE email_id = :email ORDER BY kind"),
            {"email": str(first_email)},
        )
        every_kind = [row[0] for row in all_kinds]

    assert "bcc" in every_kind  # positive control: the BCC row exists
    assert visible_kinds and "bcc" not in visible_kinds
