"""
Role: Seals the Stage A frozen-release behaviour of `--checkpoint` through the CLI on a
      synthetic frozen release (§16.6/§16.10/§16.13) — the runner-hash refusal at step 3, and
      the §16.13 refusal `no scorable hidden set` that must come BEFORE any reservation (no
      unit charged, no attempt recorded, no hidden file opened, no verdict, no per-SET line),
      with and without `--gates`.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: conftest.frozen_refusals (the recorded runs), tests.tools.mem01_verify.reference;
      the instrument's block validator (imported inside one test).
Key invariants:
  - The frozen release under test carries TAMPERED hidden hashes: had the runner opened any
    hidden file, stage 2 would have failed with a lock error, not with the scorability reason.
  - The reservation, free-repeat, exhaustion and per-SET mechanics are sealed at module level
    (test_hidden_budget.py, test_verdict.py, test_result_block.py) — §16.13 makes them
    CLI-sealable only from stage C.
"""

from __future__ import annotations

import re

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import (
    NO_SCORABLE_REASON,
    SESSION_LOOP,
    FrozenRefusalsFactory,
    InstrumentLoader,
)
from tests.tools.mem01_verify.frozen_release import BOGUS_SHA256
from tests.tools.mem01_verify.reference import CliRun

SET_LINE = re.compile(r"^(QS|NF|LANG|RET): GATES (PASS|FAIL)$")


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


def _aborted(run: CliRun) -> dict:
    assert run.exit_code == 2, run.stderr[-2000:]
    block = reference.extract_machine_block(run.stdout)
    assert block["aborted"] is True and block["status"] == "ERROR" and block["reason"]
    assert not any(line.startswith("STEP1 ") for line in run.stdout.splitlines())
    assert not any(SET_LINE.match(line) for line in run.stdout.splitlines())
    assert "HIDDEN BUDGET EXHAUSTED" not in run.stdout
    return block


@SESSION_LOOP
async def test_frozen_release_with_a_wrong_runner_hash_aborts_at_step_3_without_a_reservation(
    frozen_refusals: FrozenRefusalsFactory,
) -> None:
    scenario = await frozen_refusals()

    block = _aborted(scenario.wrong_runner_run)

    assert block["aborted_at_step"] == 3 and block["run_kind"] == "checkpoint"
    assert block["reason"] != NO_SCORABLE_REASON  # stage 1 refused first
    assert scenario.wrong_runner.ledger_path.read_bytes() == b""
    assert scenario.wrong_runner.audit_path.read_bytes() == b""


@SESSION_LOOP
async def test_checkpoint_is_refused_for_no_scorable_hidden_set_before_any_reservation(
    frozen_refusals: FrozenRefusalsFactory,
) -> None:
    scenario = await frozen_refusals()

    block = _aborted(scenario.checkpoint)

    assert block["reason"] == NO_SCORABLE_REASON and block["run_kind"] == "checkpoint"
    assert block.get("release_state") in ("frozen", None)
    assert scenario.ledger_after_checkpoint == b""  # no hidden_reservation, no charge
    assert scenario.audit_after_checkpoint == b""  # no attempt recorded
    assert all(block[key] is None for key in block if key.startswith("hidden_budget"))


@SESSION_LOOP
async def test_checkpoint_refusal_never_opened_a_hidden_file(
    frozen_refusals: FrozenRefusalsFactory,
) -> None:
    scenario = await frozen_refusals()
    files = scenario.frozen.manifest()["files"]
    hidden = {
        relative: entry for relative, entry in files.items() if entry["visibility"] == "hidden"
    }

    block = _aborted(scenario.checkpoint)

    # every hidden entry is unverifiable on disk: the test split's hashes are flipped and the
    # validation split's are bogus, so a stage-2 open could only have ended in a lock error
    assert hidden and all(relative.startswith("hidden/") for relative in hidden)
    assert all(
        entry["sha256"] != reference.sha256_hex(scenario.frozen.hidden_file(set_name).read_bytes())
        for set_name, entry in (
            (relative.split("/")[2], entry)
            for relative, entry in hidden.items()
            if relative.startswith("hidden/test/")
        )
    )
    assert all(
        entry["sha256"] == BOGUS_SHA256
        for relative, entry in hidden.items()
        if relative.startswith("hidden/validation/")
    )
    assert block["reason"] == NO_SCORABLE_REASON
    assert not (
        scenario.frozen.hidden_root / "releases" / scenario.frozen.name / "validation"
    ).exists()


@SESSION_LOOP
async def test_partial_checkpoint_is_refused_without_a_reservation(
    frozen_refusals: FrozenRefusalsFactory,
) -> None:
    scenario = await frozen_refusals()

    block = _aborted(scenario.partial_checkpoint)

    assert block["run_kind"] == "checkpoint"
    assert scenario.ledger_after_partial == b""  # §16.13: partial runs never reserve
    assert scenario.frozen.audit_path.read_bytes() == b""


@SESSION_LOOP
async def test_refusal_blocks_validate_as_aborted_stdout_projections(
    instrument: InstrumentLoader, frozen_refusals: FrozenRefusalsFactory
) -> None:
    scenario = await frozen_refusals()
    result_block = instrument("result_block")

    for run in (scenario.wrong_runner_run, scenario.checkpoint, scenario.partial_checkpoint):
        block = reference.extract_machine_block(run.stdout)
        result_block.validate_result_block(block, projection="stdout")
        assert reference.last_nonempty_line(run.stdout) == "MEM01_RESULT_V1_END"
        _assert_aborted_gates(block)
