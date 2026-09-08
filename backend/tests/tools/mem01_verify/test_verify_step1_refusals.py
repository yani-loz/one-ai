"""
Role: Seals the determined refusals of contract §16.10/§16.11 through the CLI over the probe
      corpus — an `--org` off the manifest is a roster mismatch aborted at step 4 or 5 (the
      §3.2 step tension is flagged); a release cut
      for the small org scores SNAP `ERROR` below the annex minimum and never PASS; an unknown
      `--gates` name aborts with `unknown gate: <name>`; and a probe that cannot be dropped
      because a foreign connection is live aborts the run at step 11 (`cleanup_failed`) with no
      verdict line while the protected result stays on disk.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: conftest.run_cli / draft_release / small_release / probe_corpus / probe_databases /
      owner_connection / register_probe_for_cleanup; tests.tools.mem01_verify.reference; the
      criteria annex through pyyaml.
Key invariants:
  - The foreign connection is opened by THIS process on the probe the child mints (discovered
    by polling pg_database while the run is in flight) and closed in `finally`; the leftover
    probe is registered for the suite's own FORCE cleanup.
  - Expected statuses follow from the seeded counts and the annex minimum (R12).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import (
    SESSION_LOOP,
    CliRunner,
    DraftReleaseFactory,
    ProbeCorpusFactory,
)
from tests.tools.mem01_verify.reference import CliRun

DISCOVERY_TIMEOUT_SECONDS = 600.0


def _aborted(run: CliRun, *, step: int | None = None) -> dict:
    assert run.exit_code == 2, run.stderr[-2000:]
    block = reference.extract_machine_block(run.stdout)
    assert block["status"] == "ERROR" and block["aborted"] is True and block["reason"]
    assert reference.last_nonempty_line(run.stdout) == "MEM01_RESULT_V1_END"
    assert not any(line.startswith("STEP1 ") for line in run.stdout.splitlines())
    if step is not None:
        assert block["aborted_at_step"] == step
    return block


def _completed(run: CliRun) -> tuple[dict, str]:
    block = reference.extract_machine_block(run.stdout)
    verdict = reference.last_nonempty_line(run.stdout)
    assert verdict.startswith("STEP1 ") and block["aborted"] is False
    assert sum(line.startswith("STEP1 ") for line in run.stdout.splitlines()) == 1
    return block, verdict


def _annex_minimum(criteria_yaml: dict, criterion_id: str) -> int:
    for gate in criteria_yaml["gates"].values():
        for criterion in gate["criteria"]:
            if criterion["id"] == criterion_id:
                return int(criterion["minimum"])
    raise AssertionError(f"{criterion_id} not in the annex")


@SESSION_LOOP
async def test_org_override_off_the_manifest_is_a_roster_mismatch_aborted_at_step_4_or_5(
    run_cli: CliRunner,
    draft_release: DraftReleaseFactory,
    probe_corpus: ProbeCorpusFactory,
) -> None:
    corpus = await probe_corpus()
    release = await draft_release()
    small = corpus.small

    run = await run_cli(
        ["--release", str(release.path), "--org", str(small.org_id)],
        database=release.database,
        gold_root=release.gold_root,
    )

    block = _aborted(run)  # §16.10: the corpus digest cannot match the manifest
    # §3.2 names the corpus roster in step 4 but computes CorpusIdentity in step 5 (flagged)
    assert block["aborted_at_step"] in (4, 5)
    assert block["run_kind"] == "tuning"
    assert not any(marker in run.stdout + run.stderr for marker in small.personal_markers)


@SESSION_LOOP
async def test_small_org_release_scores_snap_error_below_the_annex_minimum_never_pass(
    run_cli: CliRunner,
    small_release: DraftReleaseFactory,
    probe_corpus: ProbeCorpusFactory,
    criteria_yaml: dict,
) -> None:
    corpus = await probe_corpus()
    release = await small_release()
    small = corpus.small
    minimum = _annex_minimum(criteria_yaml, "snap.replay_hash_equality")
    assert small.text_artifact_count < minimum  # the seeded org is below the floor by design

    run = await run_cli(
        ["--release", str(release.path), "--gates", "SNAP"],
        database=release.database,
        gold_root=release.gold_root,
    )

    assert run.exit_code == 2, run.stderr[-2000:]
    block, verdict = _completed(run)
    assert block["corpus"]["org_id"] == str(small.org_id)
    assert block["corpus"]["emails"] == small.email_count
    replay = next(e for e in block["criteria"] if e["id"] == "snap.replay_hash_equality")
    assert replay["denominator"] == small.text_artifact_count
    assert replay["status"] == "ERROR" and block["gates"]["SNAP"]["status"] == "ERROR"
    assert verdict.startswith("STEP1 TUNING: 0/17 PASS | ")
    assert not any(marker in run.stdout + run.stderr for marker in small.personal_markers)


@SESSION_LOOP
async def test_unknown_gate_name_is_refused_with_the_determined_reason(
    run_cli: CliRunner, draft_release: DraftReleaseFactory
) -> None:
    release = await draft_release()

    refused = await run_cli(
        ["--release", str(release.path), "--gates", "SNAP,BOGUS"],
        database=release.database,
        gold_root=release.gold_root,
    )
    admitted = await run_cli(
        ["--release", str(release.path), "--gates", "SNAP"],
        database=release.database,
        gold_root=release.gold_root,
    )

    block = _aborted(refused)
    assert block["reason"] == "unknown gate: BOGUS"
    control, _ = _completed(admitted)  # positive control: the same option set without BOGUS
    assert control["partial"] is True and admitted.exit_code == 2


@SESSION_LOOP
async def test_a_probe_held_by_a_foreign_connection_cannot_be_dropped_and_the_run_aborts_at_step_11(
    run_cli: CliRunner,
    draft_release: DraftReleaseFactory,
    probe_databases: Callable[[], Awaitable[list[str]]],
    owner_connection: Callable[[str], Awaitable[object]],
    register_probe_for_cleanup: Callable[[str], None],
) -> None:
    release = await draft_release()
    before = set(await probe_databases())
    task = asyncio.create_task(
        run_cli(
            ["--release", str(release.path), "--gates", "VIS"],
            database=release.database,
            gold_root=release.gold_root,
        )
    )
    held, probe_name = None, None
    deadline = time.monotonic() + DISCOVERY_TIMEOUT_SECONDS
    while held is None and not task.done() and time.monotonic() < deadline:
        appeared = set(await probe_databases()) - before
        if appeared:
            probe_name = sorted(appeared)[0]
            register_probe_for_cleanup(probe_name)
            try:
                held = await owner_connection(probe_name)
            except Exception:  # noqa: BLE001 - asyncpg raises PostgresError, not OSError
                held = None  # the probe exists but does not accept connections yet
        if held is None:
            await asyncio.sleep(0.2)
    try:
        run = await task
    finally:
        if held is not None:
            await held.close()  # type: ignore[attr-defined]

    assert probe_name is not None and held is not None, "no probe appeared while the run ran"
    assert run.exit_code == 2, run.stderr[-2000:]
    assert not any(line.startswith("STEP1 ") for line in run.stdout.splitlines())
    block = reference.extract_machine_block(run.stdout)
    assert block["aborted"] is True and block["status"] == "ERROR"
    assert block["aborted_at_step"] == 11  # §3.2: a failed drop is an infrastructure error
    report_dir = release.path / "reports" / block["run_id"]
    protected = reference.read_text(report_dir / "protected_result.json")
    assert "cleanup_failed" in json.dumps(block) + protected
    if "cleanup" in block:
        assert block["cleanup"]["probe_dropped"] is False
    assert probe_name in await probe_databases()  # left behind, reported stale by the next run
