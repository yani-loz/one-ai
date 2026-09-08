"""
Role: How a run ENDS — the §3.2 step 10/11/13 half of the sequence that the printing does not
      own: whether this run may write a report directory at all (§16.14), the block it assembles
      from `RunState` (completed or aborted, §3.3), the probe drop that no path may skip
      (§16.16(j)), the hidden-ledger / validation-journal outcome record of step 10, and the
      final report writes of step 13.
Used by: `tools.mem01_verify.verify_step1`; sealed through the CLI by
      `tests/tools/mem01_verify/test_verify_step1_*.py` and `test_gates_stage_a.py`.
Depends on: `tools.mem01_verify.runner_output` (`RunState`, `HIDDEN_RUN_KINDS`, the block
      builders), `.runner_logging` (the guarded writes and `protected_result_relpath`),
      `.runner_steps` (the audit filename), `.exceptions`, and `.validation_guard` lazily.
Key invariants:
  - `drop_probe` never raises and never lets a probe leak: it closes the lease whatever the
    caller did before, and ANY exception class — a driver or OS error included — degrades to the
    `cleanup_failed` detail the caller turns into an abort reason (§16.16(j)/(t), R5, R11).
  - `record_hidden_outcome` is called only AFTER the printed shape is decided, so an aborted run
    is recorded as `failed` and reserves no verdict (§16.16(t)/A21). This module never prints.
  - Nothing is ever written under an absent hidden root: every write site upstream goes through
    `artifacts_writable` first (§16.14).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from tools.mem01_verify.exceptions import Mem01Error
from tools.mem01_verify.runner_logging import (
    protected_result_relpath,
    seal_aborted_validation_attempt,
    write_guarded,
    write_protected_result,
    write_stdout_transcript,
)
from tools.mem01_verify.runner_output import (
    HIDDEN_RUN_KINDS,
    RunState,
    build_aborted_block,
    build_completed_block,
)
from tools.mem01_verify.runner_steps import AUDIT_FILENAME

UNEXPECTED_DETAIL = "unexpected {violation}"


def artifacts_writable(state: RunState, hidden_root: Path) -> bool:
    """True when this run may write its report directory (§16.14: never before step 3)."""
    if state.release is None or state.report_dir is None:
        return False
    if state.run_kind in HIDDEN_RUN_KINDS:
        return hidden_root.is_dir()
    return True


def elapsed_ms(state: RunState) -> int:
    """The run's wall-clock length so far, in milliseconds — the block's `duration_ms`."""
    return int((datetime.now(UTC) - state.started_at).total_seconds() * 1000)


def refusal_reason(error: BaseException) -> str:
    """The block-level `reason` of a refusal — R5-safe text for a `Mem01Error`, else the class."""
    if isinstance(error, Mem01Error):
        return str(error)
    return UNEXPECTED_DETAIL.format(violation=type(error).__name__)


def assemble_block(state: RunState, abort: tuple[int, str] | None) -> dict[str, object]:
    """Build the block this run will print — the aborted shape when a step refused (R11)."""
    duration = elapsed_ms(state)
    if abort is None:
        return build_completed_block(state, duration_ms=duration)
    step, reason = abort
    return build_aborted_block(state, step=step, reason=reason, duration_ms=duration)


async def drop_probe(state: RunState, *, keep: bool) -> str | None:
    """Step 11: drop the probe unless it is kept; return the failure detail, if any.

    The lease is closed either way: the probe carries this run's `--keep-probe` value, so closing
    it drops a probe that an exception between steps 6 and 11 skipped (§16.16(j)). ANY exception
    class is caught (A27): a driver or an OS error is an infrastructure failure exactly like a
    `ProbeDatabaseError`, and the caller reports it as `cleanup_failed` rather than as a
    traceback (R11); a class name stands in for a message that is not known to be R5-safe.
    """
    detail: str | None = None
    probe = state.probe
    if probe is not None and not keep:
        try:
            await probe.drop()  # type: ignore[attr-defined]  # ProbeDatabase
        except Exception as error:  # noqa: BLE001 - R5/R11: a failed drop names its class
            detail = refusal_reason(error)
    if state.lease is not None:
        lease, state.lease = state.lease, None
        try:
            await lease.__aexit__(None, None, None)  # type: ignore[attr-defined]  # async CM
        except Exception as error:  # noqa: BLE001 - R5/R11: a failed release names its class
            detail = detail or refusal_reason(error)
    if probe is not None:
        state.probe_dropped = bool(getattr(probe, "dropped", False))
    return detail


def record_hidden_outcome(state: RunState, block: Mapping[str, object], *, aborted: bool) -> None:
    """Step 10/11: close the reservation, or reserve the validation verdict, before printing."""
    if state.budget is not None and state.reservation is not None:
        path = (
            None
            if aborted
            else protected_result_relpath(state.require_report_dir(), state.require_report_root())
        )
        state.budget.record_outcome(  # type: ignore[attr-defined]  # HiddenBudget
            state.reservation,
            outcome="failed" if aborted else "completed",
            protected_result=None if aborted else dict(block),
            protected_result_path=path,
        )
    if state.run_kind == "validation" and state.attempt_id is not None:
        from tools.mem01_verify import validation_guard

        audit = state.require_release().path / AUDIT_FILENAME
        if aborted:
            validation_guard.record_abort(audit, state.attempt_id, str(block.get("reason")))
        else:
            validation_guard.reserve_verdict(audit, state.attempt_id)


def rewrite_protected_result(state: RunState, block: Mapping[str, object]) -> str | None:
    """Rewrite `protected_result.json` so it equals the block the run reports (§16.16(n)).

    Args:
        state: The run, for its report directory.
        block: The post-cleanup block this run will print.

    Returns:
        The failing exception's CLASS name (§16.17(g)) — which the caller folds into the
        `artifacts_unwritable` abort while the run still has an outcome to decide — or None when
        the rewrite succeeded. The best-effort rewrite that follows a REFUSED projection ignores
        it: that path has aborted already.
    """
    return write_guarded(lambda: write_protected_result(state.require_report_dir(), block))


def finalize_report(state: RunState, transcript: str, *, aborted: bool) -> None:
    """Step 13: the §16.11 transcript and the §3.7 seal of an aborted validation attempt.

    Both happen once every line of the run has been printed, and both are guarded: a report the
    filesystem refuses never becomes a traceback (R11).
    """
    write_guarded(lambda: write_stdout_transcript(state.require_report_dir(), transcript))
    seal_aborted_validation_attempt(state, aborted=aborted)
