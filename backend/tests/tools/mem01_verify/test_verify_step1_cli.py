"""
Role: Seals the runner's CLI surface of contract §3.1/§3.2/§3.5/§12 through child processes over
      the probe corpus — the UTF-8 self-test line first on every run, refusals that print the
      aborted block and no verdict (missing release, wrong lock, --checkpoint/--validation on a
      draft, bad --probe-db targets), the `--gates` partial run, `--keep-probe` and the
      stale-probe report, `--report-dir`, config-hash stability, both invocation forms, and the
      untouched configured database (the `--org`, unknown-gate and cleanup refusals live in
      test_verify_step1_refusals.py).
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: conftest.run_cli / draft_release / probe_corpus / probe_databases, the instrument's
      result_block validator (imported inside tests), tests.tools.mem01_verify.reference.
Key invariants:
  - `POSTGRES_DB` names the probe in every child, so the "configured database" of each run is
    the synthetic corpus; the real corpus is never opened.
  - Every refusal test proves the same option set is admitted when the offending value is fixed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
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

SELFTEST_LINE = "MEM01 UTF-8 self-test: Здравей, свят — кирилица OK"
VOLATILE_KEYS = ("run_id", "started_at", "duration_ms")
VOLATILE_NESTED_KEYS = (("corpus", "snapshot_transaction_id"), ("cleanup", "probe_name"))


H_SPLIT_GATES = frozenset({"QS", "NF", "LANG", "RET"})


def _assert_aborted_gates(block: dict) -> None:
    """§16.14/§16.16(q): every gate is skipped; on a hidden run kind the H-split gates print
    status only, the others keep `reason == "aborted"`; on a tuning run every gate keeps it."""
    hidden_kind = block["run_kind"] in ("checkpoint", "validation")
    for gate, entry in block["gates"].items():
        assert entry["status"] == "skipped", gate
        if hidden_kind and gate in H_SPLIT_GATES:
            assert set(entry) == {"status"}, gate
        else:
            assert entry["reason"] == "aborted", gate


def _aborted(run: CliRun, *, step: int | None = None) -> dict:
    assert run.exit_code == 2, run.stderr[-2000:]
    block = reference.extract_machine_block(run.stdout)
    assert block["status"] == "ERROR" and block["aborted"] is True and block["reason"]
    assert reference.last_nonempty_line(run.stdout) == "MEM01_RESULT_V1_END"
    assert not any(line.startswith("STEP1 ") for line in run.stdout.splitlines())
    _assert_aborted_gates(block)
    if step is not None:
        assert block["aborted_at_step"] == step
    return block


def _completed(run: CliRun) -> tuple[dict, str]:
    block = reference.extract_machine_block(run.stdout)
    verdict = reference.last_nonempty_line(run.stdout)
    assert verdict.startswith("STEP1 ") and block["aborted"] is False
    assert sum(line.startswith("STEP1 ") for line in run.stdout.splitlines()) == 1
    return block, verdict


async def _probe_state(instrument: InstrumentLoader, corpus: object) -> tuple:
    sessions = instrument("db").probe_session_factories(corpus.database)  # type: ignore[attr-defined]
    async with sessions.global_() as session:
        await seeding.assert_probe_connection(session)
        version = (
            await session.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one()
        counts = (
            await session.execute(
                text("SELECT org_id, count(*) FROM email_message GROUP BY org_id ORDER BY org_id")
            )
        ).all()
    return version, tuple((str(row[0]), row[1]) for row in counts)


@SESSION_LOOP
async def test_selftest_line_is_first_on_stdout_without_env_crutches(
    run_cli: CliRunner,
    draft_release: DraftReleaseFactory,
) -> None:
    release = await draft_release()

    run = await run_cli(
        ["--release", str(release.gold_root / "releases" / "does-not-exist")],
        database=release.database,
        gold_root=release.gold_root,
    )

    assert run.stdout.splitlines()[0] == SELFTEST_LINE
    _aborted(run, step=3)


@SESSION_LOOP
async def test_missing_release_aborts_with_a_schema_valid_block(
    instrument: InstrumentLoader,
    run_cli: CliRunner,
    draft_release: DraftReleaseFactory,
) -> None:
    release = await draft_release()
    result_block = instrument("result_block")

    run = await run_cli(
        ["--release", str(release.gold_root / "releases" / "missing")],
        database=release.database,
        gold_root=release.gold_root,
    )

    block = _aborted(run, step=3)
    result_block.validate_result_block(block, projection="protected")
    assert block["run_kind"] == "tuning" and block["release_lock_sha256"] is None


@SESSION_LOOP
async def test_wrong_expect_lock_is_refused_and_the_right_one_admits(
    run_cli: CliRunner,
    draft_release: DraftReleaseFactory,
) -> None:
    release = await draft_release()
    manifest = release.path / "dataset.manifest.json"
    right = hashlib.sha256(reference.read_bytes(manifest)).hexdigest()
    wrong = right[:-1] + ("0" if right[-1] != "0" else "1")

    refused = await run_cli(
        ["--release", str(release.path), "--expect-lock", f"sha256:{wrong}", "--gates", "SNAP"],
        database=release.database,
        gold_root=release.gold_root,
    )
    admitted = await run_cli(
        ["--release", str(release.path), "--expect-lock", f"sha256:{right}", "--gates", "SNAP"],
        database=release.database,
        gold_root=release.gold_root,
    )

    _aborted(refused, step=3)
    block, _ = _completed(admitted)
    assert block["release_lock_sha256"] == right


@SESSION_LOOP
async def test_checkpoint_and_validation_are_refused_on_a_draft_before_any_charge(
    run_cli: CliRunner,
    draft_release: DraftReleaseFactory,
) -> None:
    release = await draft_release()
    audit, ledger = release.path / "audit.jsonl", release.gold_root / "hidden_budget.jsonl"
    audit_before, ledger_before = reference.read_bytes(audit), reference.read_bytes(ledger)

    checkpoint = await run_cli(
        ["--release", str(release.path), "--checkpoint"],
        database=release.database,
        gold_root=release.gold_root,
    )
    validation = await run_cli(
        ["--release", str(release.path), "--validation"],
        database=release.database,
        gold_root=release.gold_root,
    )

    assert _aborted(checkpoint, step=3)["run_kind"] == "checkpoint"
    assert _aborted(validation, step=3)["run_kind"] == "validation"
    assert reference.read_bytes(audit) == audit_before
    assert reference.read_bytes(ledger) == ledger_before


@SESSION_LOOP
async def test_probe_db_targeting_refusals_leave_the_configured_database_untouched(
    instrument: InstrumentLoader,
    run_cli: CliRunner,
    draft_release: DraftReleaseFactory,
    probe_corpus: ProbeCorpusFactory,
) -> None:
    corpus = await probe_corpus()
    release = await draft_release()
    before = await _probe_state(instrument, corpus)
    targets = ["oracle_not_a_probe", release.database, f"mem01_probe_absent{uuid4().hex[:8]}"]

    runs = [
        await run_cli(
            ["--release", str(release.path), "--gates", "VIS", "--probe-db", target],
            database=release.database,
            gold_root=release.gold_root,
        )
        for target in targets
    ]

    for run in runs:
        _aborted(run)
    assert await _probe_state(instrument, corpus) == before


@SESSION_LOOP
async def test_gates_option_marks_the_run_partial_and_skips_the_rest(
    run_cli: CliRunner,
    draft_release: DraftReleaseFactory,
) -> None:
    release = await draft_release()

    run = await run_cli(
        ["--release", str(release.path), "--gates", "SNAP"],
        database=release.database,
        gold_root=release.gold_root,
    )

    assert run.exit_code == 2
    block, verdict = _completed(run)
    assert block["partial"] is True and block["status"] == "ERROR"
    assert block["reason"] == "partial run (--gates)"
    assert block["run_kind"] == "tuning" and block["gates"]["SNAP"]["status"] == "PASS"
    skipped = {gate for gate, entry in block["gates"].items() if entry["status"] == "skipped"}
    assert skipped == set(block["gates"]) - {"SNAP"}
    assert all(block["gates"][gate]["reason"] for gate in skipped)
    assert verdict.startswith("STEP1 TUNING: 1/17 PASS | provisional=4:FID,THR,IDENT,ATTR | ")


@SESSION_LOOP
async def test_keep_probe_leaves_the_probe_and_the_next_run_reports_it_stale(
    run_cli: CliRunner,
    draft_release: DraftReleaseFactory,
    probe_databases: Callable[[], Awaitable[list[str]]],
    register_probe_for_cleanup: Callable[[str], None],
) -> None:
    release = await draft_release()

    kept = await run_cli(
        ["--release", str(release.path), "--gates", "VIS", "--keep-probe"],
        database=release.database,
        gold_root=release.gold_root,
    )
    block, _ = _completed(kept)
    name = block["cleanup"]["probe_name"]
    register_probe_for_cleanup(name)
    following = await run_cli(
        ["--release", str(release.path), "--gates", "SNAP"],
        database=release.database,
        gold_root=release.gold_root,
    )

    assert block["cleanup"]["kept"] is True and block["cleanup"]["probe_dropped"] is False
    assert name.startswith("mem01_probe_") and name in await probe_databases()
    next_block, _ = _completed(following)
    assert name in json.dumps(next_block["diagnostics"])


@SESSION_LOOP
async def test_run_without_keep_probe_drops_its_probe(
    run_cli: CliRunner,
    draft_release: DraftReleaseFactory,
    probe_databases: Callable[[], Awaitable[list[str]]],
) -> None:
    release = await draft_release()

    run = await run_cli(
        ["--release", str(release.path), "--gates", "VIS"],
        database=release.database,
        gold_root=release.gold_root,
    )

    block, _ = _completed(run)
    cleanup = block["cleanup"]
    assert cleanup["probe_dropped"] is True and cleanup["kept"] is False
    assert cleanup["probe_name"].startswith("mem01_probe_")
    assert cleanup["probe_name"] not in await probe_databases()


@SESSION_LOOP
async def test_report_dir_option_redirects_the_protected_result(
    run_cli: CliRunner,
    draft_release: DraftReleaseFactory,
    tmp_path: Path,
) -> None:
    release = await draft_release()
    report_dir = tmp_path / "reports"

    run = await run_cli(
        ["--release", str(release.path), "--gates", "SNAP", "--report-dir", str(report_dir)],
        database=release.database,
        gold_root=release.gold_root,
    )

    block, _ = _completed(run)
    written = [
        p
        for p in reference.rglob_files(report_dir, "*.json")
        if "MEM01_RESULT_V1" in reference.read_text(p)
    ]
    assert written and any(block["run_id"] in str(p) for p in written)


@SESSION_LOOP
async def test_config_hash_is_stable_for_identical_options_and_moves_with_an_option(
    run_cli: CliRunner,
    draft_release: DraftReleaseFactory,
) -> None:
    release = await draft_release()
    base = ["--release", str(release.path), "--gates", "SNAP"]

    first, _ = _completed(
        await run_cli(base, database=release.database, gold_root=release.gold_root)
    )
    second, _ = _completed(
        await run_cli(base, database=release.database, gold_root=release.gold_root)
    )
    labelled, _ = _completed(
        await run_cli(
            [*base, "--baseline-label", "x"], database=release.database, gold_root=release.gold_root
        )
    )

    assert first["config_hash"] == second["config_hash"]
    assert first["code_hash"] == second["code_hash"] == labelled["code_hash"]
    assert labelled["config_hash"] != first["config_hash"]


@SESSION_LOOP
async def test_both_invocation_forms_produce_the_same_block(
    run_cli: CliRunner,
    draft_release: DraftReleaseFactory,
) -> None:
    release = await draft_release()
    args = ["--release", str(release.path), "--gates", "SNAP"]

    module_form, _ = _completed(
        await run_cli(args, database=release.database, gold_root=release.gold_root, form="module")
    )
    script_form, _ = _completed(
        await run_cli(args, database=release.database, gold_root=release.gold_root, form="script")
    )

    for key in VOLATILE_KEYS:
        module_form.pop(key)
        script_form.pop(key)
    for section, key in VOLATILE_NESTED_KEYS:
        module_form[section].pop(key, None)
        script_form[section].pop(key, None)
    assert module_form == script_form


@SESSION_LOOP
async def test_refusal_output_carries_no_personal_data(
    run_cli: CliRunner,
    draft_release: DraftReleaseFactory,
    probe_corpus: ProbeCorpusFactory,
) -> None:
    corpus = await probe_corpus()
    release = await draft_release()

    run = await run_cli(
        ["--release", str(release.path), "--checkpoint"],
        database=release.database,
        gold_root=release.gold_root,
    )

    output = run.stdout + run.stderr
    assert "@" not in output
    assert not any(marker in output for marker in corpus.big.personal_markers)
