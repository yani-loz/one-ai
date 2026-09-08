"""
Role: Seals the probe ownership rules of contract §12/§16.4 through the CLI — a `--probe-db`
      target whose owner marker is unreleased and held by a live process is refused; after the
      creator's `release()` the same target is admitted, the reuser writes its own marker row,
      never drops the probe, and the creator still drops it at the end.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: conftest.run_cli / draft_release / probe_corpus / probe_databases /
      register_probe_for_cleanup; tools.mem01_verify.probe_db and .db (imported inside the test);
      tests.tools.mem01_verify.seeding (the §16.11 database guard).
Key invariants:
  - The reused probe is a SECOND probe this test creates in-process; the child's configured
    database stays the session corpus probe, so the two names always differ (§12).
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import text

from tests.tools.mem01_verify import reference, seeding
from tests.tools.mem01_verify.conftest import (
    SESSION_LOOP,
    CliRunner,
    DraftReleaseFactory,
    InstrumentLoader,
    ProbeCorpusFactory,
)
from tests.tools.mem01_verify.reference import CliRun


def _aborted(run: CliRun) -> dict:
    assert run.exit_code == 2, run.stderr[-2000:]
    block = reference.extract_machine_block(run.stdout)
    assert block["aborted"] is True and block["status"] == "ERROR"
    assert not any(line.startswith("STEP1 ") for line in run.stdout.splitlines())
    return block


def _completed_partial(run: CliRun) -> dict:
    assert run.exit_code == 2, run.stderr[-2000:]
    block = reference.extract_machine_block(run.stdout)
    assert block["aborted"] is False and block["partial"] is True
    assert reference.last_nonempty_line(run.stdout).startswith("STEP1 TUNING: ")
    return block


@SESSION_LOOP
async def test_probe_db_reuse_is_refused_while_the_creator_holds_it_and_admitted_after_release(
    instrument: InstrumentLoader,
    run_cli: CliRunner,
    draft_release: DraftReleaseFactory,
    probe_corpus: ProbeCorpusFactory,
    probe_databases: Callable[[], Awaitable[list[str]]],
    register_probe_for_cleanup: Callable[[str], None],
) -> None:
    await probe_corpus()
    release = await draft_release()
    probe_db = instrument("probe_db")
    db = instrument("db")
    stamp = datetime.now(UTC).strftime("%Y%m%dt%H%M%Sz")
    manager = probe_db.create_probe_database(
        reference.oracle_run_id(int(uuid4().hex[:8], 16), stamp=stamp)
    )
    probe = await manager.__aenter__()
    register_probe_for_cleanup(probe.name)
    args = ["--release", str(release.path), "--gates", "VIS", "--probe-db", probe.name]
    try:
        held = await run_cli(args, database=release.database, gold_root=release.gold_root)
        _aborted(held)
        assert probe.name in await probe_databases()
        await probe.release()

        reused = await run_cli(args, database=release.database, gold_root=release.gold_root)

        block = _completed_partial(reused)
        assert block["cleanup"]["probe_name"] == probe.name
        assert block["cleanup"]["probe_dropped"] is False
        assert probe.name in await probe_databases()
        async with db.probe_session_factories(probe.name).global_() as session:
            await seeding.assert_probe_connection(session)
            rows = (
                await session.execute(text("SELECT run_id, pid, released FROM mem01_probe_owner"))
            ).all()
        assert len(rows) == 1 and rows[0][0] == block["run_id"]
        assert rows[0][1] != os.getpid() and rows[0][2] is False
    finally:
        await manager.__aexit__(None, None, None)
    assert probe.name not in await probe_databases()
