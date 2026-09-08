"""
Role: Seals fix-registry row A30 / contract §16.17(d) at the RUNNER level, with a REAL open
      capture — the step-11 folding reads `runner_logging.app_log_emit_failures()` BEFORE the
      capture closes: an emit failure of any class in the builtin `OSError` family (`OSError`,
      `PermissionError`) aborts the run as `artifacts_unwritable: app_log <class>` (step 11, exit
      2, no verdict), while a formatting `TypeError` is evidence only — the run completes and
      `diagnostics.run.app_log_emit_failures == ["TypeError"]`.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.verify_step1, .runner_logging, .runner_output, .runner_render,
      .lock, .criteria, .corpus_identity, .gates.context (imported inside each test through the
      `instrument` loader); tests.tools.mem01_verify.review_round_5_harness and .reference.
Key invariants:
  - No `app_log_emit_failures` stand-in: the capture the runner closes at step 11 is the real
    `capture_app_logging` parked on `state.log_capture` the way `open_app_log` parks it, the
    failure is injected by breaking the `app` handler's stream (or by a record with too few
    args) and the runner's own read decides the outcome — a runner that reads AFTER
    `close_app_log` cannot pass.
  - Every marker reaches the handler only through `args`; the real file object the broken
    stream displaces is closed by the test itself.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify import review_round_5_harness as harness
from tests.tools.mem01_verify.conftest import InstrumentLoader

MARKER = "oracle-round5-runner-emit-marker-c3d1"


async def _drive_with_an_open_capture(
    instrument: InstrumentLoader,
    criteria_path: Path,
    tmp_path: Path,
    inject: Callable[[], object],
) -> tuple[int, str]:
    """Open the real capture on the run, inject one emit failure, then run steps 10-13."""
    runner_logging = instrument("runner_logging")
    release_dir = tmp_path / "release"
    state = harness.completed_run_state(instrument, criteria_path, release_dir)
    stack = contextlib.ExitStack()
    stack.enter_context(runner_logging.capture_app_logging(state.report_dir))  # type: ignore[attr-defined]
    state.log_capture = stack  # type: ignore[attr-defined]
    displaced = inject()

    code, transcript = await harness.drive_finish(
        instrument, state, ["--release", str(release_dir)], release_dir / "absent-hidden"
    )
    if displaced is not None:
        displaced.close()  # type: ignore[attr-defined]
    return code, transcript


def _stream_failure(failure: BaseException) -> Callable[[], object]:
    def inject() -> object:
        displaced = harness.break_app_log_stream(failure)
        logging.getLogger("app.round5").warning("%s", MARKER)
        return displaced

    return inject


def _formatting_failure() -> object:
    logging.getLogger("app.round5").warning("%s %s", MARKER)  # too few args: TypeError
    return None


@pytest.mark.parametrize(
    ("failure", "class_name"),
    [
        pytest.param(OSError(28, "no space left on device"), "OSError", id="OSError"),
        pytest.param(PermissionError(13, "denied"), "PermissionError", id="PermissionError"),
    ],
)
async def test_an_os_error_family_emit_failure_aborts_the_run_at_step_11_as_artifacts_unwritable(
    instrument: InstrumentLoader,
    criteria_path: Path,
    tmp_path: Path,
    failure: BaseException,
    class_name: str,
) -> None:
    code, transcript = await _drive_with_an_open_capture(
        instrument, criteria_path, tmp_path, _stream_failure(failure)
    )

    block = reference.extract_machine_block(transcript)
    assert block["aborted"] is True, "the emit failure did not abort the run"
    assert block["aborted_at_step"] == harness.CLEANUP_STEP
    assert block["reason"] == f"artifacts_unwritable: app_log {class_name}"
    assert harness.verdict_lines(transcript) == []
    assert code == 2


async def test_a_formatting_type_error_is_evidence_only_and_the_run_completes(
    instrument: InstrumentLoader, criteria_path: Path, tmp_path: Path
) -> None:
    code, transcript = await _drive_with_an_open_capture(
        instrument, criteria_path, tmp_path, _formatting_failure
    )

    block = reference.extract_machine_block(transcript)
    assert block["aborted"] is False
    assert block["diagnostics"]["run"]["app_log_emit_failures"] == ["TypeError"]
    assert len(harness.verdict_lines(transcript)) == 1
    assert code == 2  # the hand-built run's gates are all incomplete: ERROR, exit 2
