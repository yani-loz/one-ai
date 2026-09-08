"""
Role: Seals fix-registry row A33 / contract §16.17(g) on the HIDDEN run kinds — the final
      rewrite is checked BEFORE the outcome is recorded: with the rewrite failing after the
      step-10 write, a validation-shaped run prints the aborted block (`artifacts_unwritable:
      OSError`, step 11, exit 2, no verdict) and its journal's last event for the attempt is
      `validation_abort` — never `validation_verdict_reserved`; a checkpoint-shaped run records
      its reservation's outcome as `failed` and nothing else — never `completed`, and never
      `completed` THEN `failed`; and when the rewrite succeeds the same checkpoint drive
      completes and records exactly one `completed` outcome with a result digest (control).
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.verify_step1, .runner_cleanup, .runner_logging, .runner_output,
      .runner_render, .validation_guard, .hidden_budget, .audit_file (imported inside each test
      through the `instrument` loader); tests.tools.mem01_verify.review_round_5_harness and
      .reference; pytest monkeypatch.
Key invariants:
  - The hidden root EXISTS and the report directory lies under it, so the run's artifacts are
    written and the step-12 rewrite is really attempted (`len(calls) >= 2` is asserted).
  - The checkpoint budget is constructed WITH `results_root=<hidden root>`, so an outcome
    recorded EARLY (before the rewrite is checked) LANDS in the ledger and shows up as
    `["completed", "failed"]`; a budget without a root would refuse that early `completed` and
    let a candidate that suppresses the refusal pass. Both checkpoint tests therefore need
    §16.17(h) as well and are red today on the missing keyword.
  - The control asserts neither the exit code nor a verdict line: with every gate `incomplete`
    the completed block's status is ERROR and the exit is 2 even on a clean run.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify import review_round_5_harness as harness
from tests.tools.mem01_verify.conftest import InstrumentLoader

QS = hashlib.sha256(b"split QS round 5 rewrite").hexdigest()
FOUNDER = "founder"
SESSION = "oracle-session-round-5"
HEX64 = re.compile(r"[0-9a-f]{64}")


def _attempt_events(audit: Path, attempt_id: str) -> list[dict]:
    return [e for e in reference.read_jsonl(audit) if e.get("attempt_id") == attempt_id]


def _outcomes(ledger: Path) -> list[dict]:
    return [e for e in reference.read_jsonl(ledger) if e.get("type") == "hidden_outcome"]


async def test_a_failed_final_rewrite_on_a_validation_run_records_an_abort_never_a_reserved_verdict(
    instrument: InstrumentLoader,
    criteria_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = instrument("validation_guard")
    release_dir, hidden_root = tmp_path / "release", tmp_path / "hidden"
    release_dir.mkdir()
    hidden_root.mkdir()
    state = harness.completed_run_state(
        instrument, criteria_path, release_dir, run_kind="validation", report_root=hidden_root
    )
    audit = release_dir / "audit.jsonl"
    state.attempt_id = guard.record_admission(  # type: ignore[attr-defined]
        audit,
        lock_sha256=harness.HEX,
        code_hash=harness.HEX,
        config_hash=harness.HEX,
        principal=FOUNDER,
        session=SESSION,
        run_id=state.run_id,  # type: ignore[attr-defined]
    )
    writer, calls = harness.failing_after_the_first_write(instrument)
    harness.patch_protected_result_writer(monkeypatch, instrument, writer)

    code, transcript = await harness.drive_finish(
        instrument, state, ["--release", str(release_dir), "--validation"], hidden_root
    )

    assert len(calls) >= 2, "the step-12 rewrite was never attempted"  # harness control
    block = reference.extract_machine_block(transcript)
    assert block["aborted"] is True, "a failed final rewrite did not abort the validation run"
    assert block["aborted_at_step"] == harness.CLEANUP_STEP
    assert block["reason"] == "artifacts_unwritable: OSError"
    assert harness.verdict_lines(transcript) == []
    assert code == 2
    events = _attempt_events(audit, state.attempt_id)  # type: ignore[attr-defined]
    assert [e["type"] for e in events][-1] == "validation_abort"
    assert "validation_verdict_reserved" not in {e["type"] for e in events}


def _checkpoint_state(
    instrument: InstrumentLoader, criteria_path: Path, tmp_path: Path
) -> tuple[object, Path, Path, Path]:
    """A checkpoint-shaped completed run holding a reservation on a ledger under `<tmp>/gold`
    whose budget stores results under `<tmp>/hidden`; returns (state, ledger, release, root)."""
    hidden_budget = instrument("hidden_budget")
    gold_root, hidden_root = tmp_path / "gold", tmp_path / "hidden"
    release_dir = gold_root / "releases" / harness.RELEASE_NAME
    release_dir.mkdir(parents=True)
    hidden_root.mkdir()
    ledger = gold_root / "hidden_budget.jsonl"
    ledger.write_bytes(b"")
    state = harness.completed_run_state(
        instrument, criteria_path, release_dir, run_kind="checkpoint", report_root=hidden_root
    )
    budget = hidden_budget.HiddenBudget(ledger, results_root=hidden_root)  # early outcomes land
    state.reservation = budget.reserve(  # type: ignore[attr-defined]
        lock_sha256=harness.HEX,
        split_digests={"QS": QS},
        code_hash=harness.HEX,
        config_hash=harness.HEX,
        run_id=state.run_id,  # type: ignore[attr-defined]
    )
    state.budget = budget  # type: ignore[attr-defined]
    state.hidden = budget.counters({"QS": QS}, lock_sha256=harness.HEX)  # type: ignore[attr-defined]
    return state, ledger, release_dir, hidden_root


def _recording_pass_through_writer(
    instrument: InstrumentLoader,
) -> tuple[Callable[[Path, object], object], list[Path]]:
    """The real `write_protected_result`, recording its calls so the rewrite is proven attempted."""
    real_write = instrument("runner_logging").write_protected_result
    calls: list[Path] = []

    def write(report_dir: Path, block: object) -> object:
        calls.append(report_dir)
        return real_write(report_dir, block)

    return write, calls


async def test_a_failed_final_rewrite_on_a_checkpoint_run_records_a_failed_outcome_never_completed(
    instrument: InstrumentLoader,
    criteria_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, ledger, release_dir, hidden_root = _checkpoint_state(instrument, criteria_path, tmp_path)
    writer, calls = harness.failing_after_the_first_write(instrument)
    harness.patch_protected_result_writer(monkeypatch, instrument, writer)

    code, transcript = await harness.drive_finish(
        instrument, state, ["--release", str(release_dir), "--checkpoint"], hidden_root
    )

    assert len(calls) >= 2, "the step-12 rewrite was never attempted"  # harness control
    block = reference.extract_machine_block(transcript)
    assert block["aborted"] is True, "a failed final rewrite did not abort the checkpoint run"
    assert block["reason"] == "artifacts_unwritable: OSError"
    assert harness.verdict_lines(transcript) == []
    assert code == 2
    recorded = [e["outcome"] for e in _outcomes(ledger)]
    assert recorded == ["failed"], recorded  # never `completed`, never `completed` then `failed`
    assert _outcomes(ledger)[0]["protected_result_sha256"] is None


async def test_a_successful_final_rewrite_on_a_checkpoint_run_records_exactly_one_completed_outcome(
    instrument: InstrumentLoader,
    criteria_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, ledger, release_dir, hidden_root = _checkpoint_state(instrument, criteria_path, tmp_path)
    writer, calls = _recording_pass_through_writer(instrument)
    harness.patch_protected_result_writer(monkeypatch, instrument, writer)

    _, transcript = await harness.drive_finish(
        instrument, state, ["--release", str(release_dir), "--checkpoint"], hidden_root
    )

    assert len(calls) >= 2, "the step-12 rewrite was never attempted"  # harness control
    block = reference.extract_machine_block(transcript)
    assert block["aborted"] is False, block.get("reason")
    recorded = [e["outcome"] for e in _outcomes(ledger)]
    assert recorded == ["completed"], recorded  # positive control of the ordering seal
    assert HEX64.fullmatch(str(_outcomes(ledger)[0]["protected_result_sha256"]))
