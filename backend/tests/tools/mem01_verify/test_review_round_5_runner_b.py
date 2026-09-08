"""
Role: Seals fix-registry rows A33, A36 / contract §16.17(g)(i) on the runner — (g)
      `runner_cleanup.rewrite_protected_result` returns the failing class name (`None` on
      success) and a failed FINAL rewrite aborts a tuning run that would otherwise complete as
      `artifacts_unwritable: <class>` (`aborted_at_step: 11`, exit 2, no verdict); (i) a free
      repeat whose probe cannot be dropped prints the aborted block (`aborted_at_step: 11`,
      `cleanup_failed: …`) and exits 2, while a clean drop replays the recorded block alone with
      the recorded status's exit code. Part `_a` seals (e) and (f); part `_c` seals (g)'s
      outcome order on the hidden run kinds.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.verify_step1, .runner_cleanup, .runner_logging, .runner_output,
      .runner_render, .exceptions (imported inside each test through the `instrument` loader);
      tests.tools.mem01_verify.review_round_5_harness, .reference and .result_block_samples;
      pytest monkeypatch.
Key invariants:
  - The final-rewrite fake lets the step-10 write through and fails only the rewrite, so today's
    red is a COMPLETED block plus a verdict — not the existing step-10 `artifacts_unwritable`.
  - The free-repeat sample is the all-PASS checkpoint block, so a clean replay exits 0 and an
    exit 2 can only come from the aborted path.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify import review_round_5_harness as harness
from tests.tools.mem01_verify.conftest import InstrumentLoader
from tests.tools.mem01_verify.result_block_samples import completed_block, make_all_pass

PROBE_NAME = "mem01_probe_20260906t120000z_0a1b2c5d"


class _StubProbe:
    """A probe whose drop is recorded (and, when asked, refused)."""

    name = PROBE_NAME
    owns_lifecycle = True

    def __init__(self, refuse_with: BaseException | None = None) -> None:
        self.drop_calls = 0
        self.dropped = False
        self._refuse_with = refuse_with

    async def drop(self) -> None:
        self.drop_calls += 1
        if self._refuse_with is not None:
            raise self._refuse_with
        self.dropped = True


# ── (g) the final rewrite ────────────────────────────────────────────────────────────────


def _raising(exception: BaseException) -> Callable[..., object]:
    def write(*args: object, **kwargs: object) -> None:
        raise exception

    return write


def test_rewrite_protected_result_returns_the_failing_class_or_none(
    instrument: InstrumentLoader, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner_cleanup = instrument("runner_cleanup")
    state = instrument("runner_output").RunState(
        run_kind="tuning",
        run_id=reference.oracle_run_id(40),
        started_at=datetime.now(UTC),
        partial=False,
        baseline_label=None,
    )
    state.report_dir = tmp_path / "run"
    block = {"schema": "MEM01_RESULT_V1", "aborted": True, "reason": "oracle"}

    success = runner_cleanup.rewrite_protected_result(state, block)
    harness.patch_protected_result_writer(monkeypatch, instrument, _raising(OSError("denied")))
    os_error = runner_cleanup.rewrite_protected_result(state, block)
    harness.patch_protected_result_writer(
        monkeypatch, instrument, _raising(TypeError("unserialisable"))
    )
    type_error = runner_cleanup.rewrite_protected_result(state, block)

    assert success is None and (tmp_path / "run" / "protected_result.json").is_file()
    assert os_error == "OSError"
    assert type_error == "TypeError"


async def test_a_failed_final_rewrite_aborts_the_run_as_artifacts_unwritable(
    instrument: InstrumentLoader,
    criteria_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer, calls = harness.failing_after_the_first_write(instrument)
    harness.patch_protected_result_writer(monkeypatch, instrument, writer)
    release_dir = tmp_path / "release"
    state = harness.completed_run_state(instrument, criteria_path, release_dir)

    code, transcript = await harness.drive_finish(
        instrument, state, ["--release", str(release_dir)], release_dir / "absent-hidden"
    )

    assert len(calls) >= 2, "the step-12 rewrite was never attempted"  # harness control
    block = reference.extract_machine_block(transcript)
    assert block["aborted"] is True, "a failed final rewrite did not abort the run"
    assert block["aborted_at_step"] == harness.CLEANUP_STEP
    assert block["reason"] == "artifacts_unwritable: OSError"
    assert harness.verdict_lines(transcript) == []
    assert code == 2


# ── (i) the free repeat ──────────────────────────────────────────────────────────────────


async def _drive_free_repeat(
    instrument: InstrumentLoader, probe: _StubProbe, recorded: dict, tmp_path: Path
) -> tuple[int, str]:
    verify_step1 = instrument("verify_step1")
    state = instrument("runner_output").RunState(
        run_kind="checkpoint",
        run_id=reference.oracle_run_id(42),
        started_at=datetime.now(UTC),
        partial=False,
        baseline_label=None,
    )
    state.probe, state.probe_name = probe, probe.name
    out = instrument("runner_render").TeeStream(io.StringIO())

    code = await verify_step1._finish_free_repeat(state, out, tmp_path / "absent-hidden", recorded)
    return code, out.transcript


async def test_a_free_repeat_whose_probe_cannot_be_dropped_is_aborted_at_step_11(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    exceptions = instrument("exceptions")
    refusing = _StubProbe(refuse_with=exceptions.ProbeDatabaseError("backends still connected"))
    recorded = make_all_pass(completed_block("checkpoint"), keep_reason=False)

    code, transcript = await _drive_free_repeat(instrument, refusing, recorded, tmp_path)

    assert refusing.drop_calls >= 1  # harness control: the drop was attempted and refused
    block = reference.extract_machine_block(transcript)
    assert block["aborted"] is True, "a refused drop did not abort the free repeat"
    assert block["aborted_at_step"] == harness.CLEANUP_STEP
    assert str(block["reason"]).startswith("cleanup_failed")
    assert harness.verdict_lines(transcript) == []
    assert code == 2


async def test_a_free_repeat_with_a_clean_drop_replays_the_recorded_block(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    probe = _StubProbe()
    recorded = make_all_pass(completed_block("checkpoint"), keep_reason=False)

    code, transcript = await _drive_free_repeat(instrument, probe, recorded, tmp_path)

    block = reference.extract_machine_block(transcript)
    assert probe.drop_calls >= 1 and probe.dropped is True
    assert block["aborted"] is False and block["status"] == "PASS"
    assert block["run_id"] == recorded["run_id"]  # the RECORDED block, not this run's
    assert harness.verdict_lines(transcript) == []  # the replay prints the recorded block alone
    assert code == 0
