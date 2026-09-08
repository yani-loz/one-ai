"""
Role: Seals the probe-database lifecycle of contract §12 / §1.3 `probe_db` — preflight of the
      runtime roles and CREATE DATABASE right, creation under the `mem01_probe_` prefix migrated
      to the repository's head revision, an empty tenant start, the `current_database()`
      binding, the drop on exit (verified independently in pg_database), and the stale-probe
      listing shape.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.probe_db and .db (imported inside each test); pg_database reads
      on the maintenance database (conftest.probe_databases); the migrations directory for the
      expected head revision.
Key invariants:
  - The head revision is derived from the migration files, never asked of the instrument.
  - The 16.4 owner marker (`mem01_probe_owner`, one row) is read with plain SQL on the
    probe; `release()` and `drop()` are async (§16.13) and awaited directly.
  - This module creates ONE extra probe beyond the session probe and proves it is gone after.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.tools.mem01_verify import reference, seeding
from tests.tools.mem01_verify.conftest import SESSION_LOOP, InstrumentLoader, ProbeCorpusFactory

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "app" / "db" / "migrations" / "versions"
RUNTIME_ROLES = ("oneai_app", "oneai_global", "oneai_reader")


def repository_head_revision() -> str:
    """The revision no migration file names as its down_revision (single-head chain)."""
    return reference.repository_head_revision(MIGRATIONS_DIR)


def test_repository_has_a_single_migration_head() -> None:
    """Helper proof (passes today): the expected-head computation is unambiguous."""
    assert repository_head_revision().startswith("00")


def test_probe_prefix_constant(instrument: InstrumentLoader) -> None:
    assert instrument("probe_db").PROBE_PREFIX == "mem01_probe_"


@SESSION_LOOP
async def test_preflight_reports_roles_logins_and_create_right(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory
) -> None:
    await probe_corpus()  # the dev server is reachable and the instrument is importable
    probe_db = instrument("probe_db")

    preflight = await probe_db.preflight_probe_server()

    assert preflight.roles_ok is True and preflight.missing_roles == ()
    assert set(preflight.login_ok) >= set(RUNTIME_ROLES)
    assert all(preflight.login_ok[role] is True for role in RUNTIME_ROLES)
    assert preflight.can_create_database is True
    assert all(name.startswith("mem01_probe_") for name in preflight.stale_probes)


@SESSION_LOOP
async def test_create_probe_database_migrates_to_head_starts_empty_owns_its_marker_and_drops(
    instrument: InstrumentLoader,
    probe_corpus: ProbeCorpusFactory,
    probe_databases: Callable[[], Awaitable[list[str]]],
) -> None:
    await probe_corpus()
    probe_db = instrument("probe_db")
    db = instrument("db")
    run_id = reference.oracle_run_id(int(uuid4().hex[:8], 16))
    expected_name = f"mem01_probe_{run_id}"
    assert expected_name not in await probe_databases()

    async with probe_db.create_probe_database(run_id) as probe:
        assert probe.name == expected_name and probe.run_id == run_id
        assert isinstance(probe.created_at, datetime) and probe.created_at.tzinfo is not None
        assert probe.migrated_to == repository_head_revision()
        assert expected_name in await probe_databases()
        sessions = db.probe_session_factories(probe.name)
        async with sessions.global_() as session:
            database = (await session.execute(text("SELECT current_database()"))).scalar_one()
            version = (
                await session.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
            await seeding.assert_probe_connection(session)
            emails = (
                await session.execute(text("SELECT count(*) FROM email_message"))
            ).scalar_one()
            orgs = (await session.execute(text("SELECT count(*) FROM organizations"))).scalar_one()
            owner_rows = (
                await session.execute(
                    text("SELECT run_id, pid, created_at, released FROM mem01_probe_owner")
                )
            ).all()
        assert database == expected_name and version == repository_head_revision()
        assert emails == 0 and orgs == 0
        assert len(owner_rows) == 1 and owner_rows[0][0] == run_id
        assert owner_rows[0][1] == os.getpid() and owner_rows[0][3] is False
        assert owner_rows[0][2].tzinfo is not None
        await probe.release()
        async with sessions.global_() as session:
            flags = (await session.execute(text("SELECT released FROM mem01_probe_owner"))).all()
        assert [row[0] for row in flags] == [True]

    assert expected_name not in await probe_databases()


@SESSION_LOOP
async def test_list_stale_probe_databases_returns_prefixed_names_only(
    instrument: InstrumentLoader,
    probe_corpus: ProbeCorpusFactory,
    probe_databases: Callable[[], Awaitable[list[str]]],
) -> None:
    await probe_corpus()
    probe_db = instrument("probe_db")

    stale = await probe_db.list_stale_probe_databases()

    assert isinstance(stale, list)
    assert all(name.startswith("mem01_probe_") for name in stale)
    assert set(stale) <= set(await probe_databases())


def test_probe_database_error_is_a_mem01_error(instrument: InstrumentLoader) -> None:
    exceptions = instrument("exceptions")

    assert issubclass(exceptions.ProbeDatabaseError, exceptions.Mem01Error)


@SESSION_LOOP
async def test_drop_refuses_while_a_foreign_connection_is_open_and_never_uses_force(
    instrument: InstrumentLoader,
    probe_corpus: ProbeCorpusFactory,
    probe_databases: Callable[[], Awaitable[list[str]]],
    owner_connection: Callable[[str], Awaitable[object]],
    register_probe_for_cleanup: Callable[[str], None],
) -> None:
    await probe_corpus()
    probe_db = instrument("probe_db")
    exceptions = instrument("exceptions")
    manager = probe_db.create_probe_database(reference.oracle_run_id(int(uuid4().hex[:8], 16)))
    probe = await manager.__aenter__()
    register_probe_for_cleanup(probe.name)
    foreign = await owner_connection(probe.name)
    try:
        with pytest.raises(exceptions.ProbeDatabaseError):
            await probe.drop()
        assert probe.name in await probe_databases()
    finally:
        await foreign.close()  # type: ignore[attr-defined]

    await manager.__aexit__(None, None, None)  # positive control: droppable once released

    assert probe.name not in await probe_databases()


@SESSION_LOOP
async def test_create_probe_database_refuses_a_run_id_outside_the_determined_grammar(
    instrument: InstrumentLoader,
    probe_corpus: ProbeCorpusFactory,
    probe_databases: Callable[[], Awaitable[list[str]]],
) -> None:
    await probe_corpus()
    probe_db = instrument("probe_db")
    exceptions = instrument("exceptions")
    before = set(await probe_databases())
    malformed = (
        "oracle_run_0001",
        "20260906T120000Z_0a1b2c3d",
        "20260906t120000z_0A1B2C3D",
        "20260906t120000z_0a1b2c3",
    )

    for run_id in malformed:
        with pytest.raises(exceptions.ProbeDatabaseError):
            async with probe_db.create_probe_database(run_id):
                pass

    assert set(await probe_databases()) == before
