"""
Role: Seals fix-registry row A3 through the CLI — a tuning, `--checkpoint` or `--validation`
      run on a FROZEN release WITHOUT `--expect-lock` is refused at step 3 (aborted, exit 2,
      reason `expect-lock required on a frozen release`) before any roster, reservation,
      admission or database access: the ledger and the audit journal stay byte-identical, no
      hidden file is opened, and the refusal is the same when the configured server is
      UNREACHABLE (port 1), so no connection was attempted. With `--expect-lock` the same
      release proceeds to the §16.13/§16.14 refusal.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: conftest.frozen_release / draft_release / frozen_refusals / run_cli;
      tests.tools.mem01_verify.reference.
Key invariants:
  - The frozen release is the `unopenable` variant: its hidden hashes are flipped, so any hidden
    open would have surfaced as a lock error rather than the expect-lock reason.
  - The positive control is the sealed `frozen_refusals.checkpoint` run (WITH `--expect-lock`),
    which reaches the step-6 scorability refusal on the very same release.
"""

from __future__ import annotations

import re

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import (
    NO_SCORABLE_REASON,
    SESSION_LOOP,
    CliRunner,
    DraftReleaseFactory,
    FrozenRefusalsFactory,
    FrozenReleaseFactory,
)
from tests.tools.mem01_verify.reference import CliRun

EXPECT_LOCK_REASON = "expect-lock required on a frozen release"
UNREACHABLE_PORT = "1"  # nothing listens there: any connection attempt fails before step 3
SET_LINE = re.compile(r"^(QS|NF|LANG|RET): GATES (PASS|FAIL)$")
RUN_KINDS = [("tuning", []), ("checkpoint", ["--checkpoint"]), ("validation", ["--validation"])]


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
    assert reference.last_nonempty_line(run.stdout) == "MEM01_RESULT_V1_END"
    lines = run.stdout.splitlines()
    assert not any(line.startswith("STEP1 ") for line in lines)
    assert not any(SET_LINE.match(line) for line in lines)
    assert "HIDDEN BUDGET" not in run.stdout
    _assert_aborted_gates(block)
    return block


@SESSION_LOOP
@pytest.mark.parametrize(("run_kind", "extra"), RUN_KINDS)
async def test_frozen_release_without_expect_lock_is_refused_at_step_3_before_any_record(
    run_cli: CliRunner,
    frozen_release: FrozenReleaseFactory,
    draft_release: DraftReleaseFactory,
    run_kind: str,
    extra: list[str],
) -> None:
    draft = await draft_release()
    frozen = await frozen_release("test", variant="unopenable")
    ledger_before = frozen.ledger_path.read_bytes()
    audit_before = frozen.audit_path.read_bytes()

    run = await run_cli(
        ["--release", str(frozen.path), *extra],
        database=draft.database,
        gold_root=frozen.gold_root,
        hidden_root=frozen.hidden_root,
    )

    block = _aborted(run)
    assert block["reason"] == EXPECT_LOCK_REASON
    assert block["aborted_at_step"] == 3 and block["run_kind"] == run_kind
    assert block.get("release_state") in ("frozen", None)
    assert frozen.ledger_path.read_bytes() == ledger_before  # no reservation
    assert frozen.audit_path.read_bytes() == audit_before  # no admission, no attempt
    assert not (frozen.hidden_root / "releases" / frozen.name / "validation").exists()


@SESSION_LOOP
async def test_expect_lock_refusal_precedes_any_database_access_and_the_lock_unlocks_step_6(
    run_cli: CliRunner,
    frozen_release: FrozenReleaseFactory,
    draft_release: DraftReleaseFactory,
    frozen_refusals: FrozenRefusalsFactory,
) -> None:
    draft = await draft_release()
    frozen = await frozen_release("test", variant="unopenable")
    scenario = await frozen_refusals()

    unreachable = await run_cli(
        ["--release", str(frozen.path), "--checkpoint"],
        database=draft.database,
        gold_root=frozen.gold_root,
        hidden_root=frozen.hidden_root,
        extra_env={"POSTGRES_PORT": UNREACHABLE_PORT},
    )

    block = _aborted(unreachable)
    assert block["reason"] == EXPECT_LOCK_REASON and block["aborted_at_step"] == 3
    # positive control: WITH --expect-lock the same release gets past step 3 (§16.14: step 6)
    control = reference.extract_machine_block(scenario.checkpoint.stdout)
    assert control["reason"] == NO_SCORABLE_REASON and control["aborted_at_step"] == 6
    assert "--expect-lock" in scenario.checkpoint.argv
