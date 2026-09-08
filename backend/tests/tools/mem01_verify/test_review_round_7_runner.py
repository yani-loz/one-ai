"""
Role: Seals fix-registry rows A42, A45 / contract §16.17(d)(e) on the runner — (d) a failure while
      CLOSING the app-log capture (a `flush()`/`close()` that raises) never escapes:
      `runner_logging.close_app_log(state)` returns the failing exception's most specific BUILTIN
      class name (`None` on a clean close, `None` again on a repeat) and the runner folds it at
      step 11 exactly like an emit failure — an `OSError`-family close failure aborts the run
      (`artifacts_unwritable: app_log <class>`, step 11, exit 2, no verdict, no traceback on
      either stream) while any other class is evidence only (the run completes with
      `diagnostics.run.app_log_emit_failures == ["<class>"]`); (e) `main` reports an
      `asyncio.CancelledError` raised outside the §3.2 sequence as
      `MEM01 INTERNAL ERROR: CancelledError`, exit 2, no traceback. The free-repeat half of (d)
      (row A43) is sealed by `test_review_round_7_runner_b.py`.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.verify_step1, .runner_logging, .runner_output, .runner_render,
      .lock, .criteria, .corpus_identity, .gates.context (imported inside each test through the
      `instrument` loader); tests.tools.mem01_verify.review_round_5_harness (unmodified) and
      .reference; pytest capfd and monkeypatch.
Key invariants:
  - The capture the runner closes is the REAL `capture_app_logging` parked on `state.log_capture`
    the way `open_app_log` parks it; the close failure is injected by replacing the `app`
    handler's stream with one whose writes succeed and whose `flush()`/`close()` raise, and the
    test emits nothing. Should the runner itself emit an `app.*` record in steps 10-13, the
    stream's flush records the same class as an emit failure and every outcome below is
    unchanged (sorted, distinct classes; the same abort).
  - Today the close failure escapes `_finish`; the drives catch that escape and fail with its
    class name, which doubles as the proof that the stream reaches `FileHandler.close`.
  - The real file object the failing stream displaces is closed by the test itself.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify import review_round_5_harness as harness
from tests.tools.mem01_verify.conftest import InstrumentLoader

NO_SPACE = (28, "no space left on device")
DENIED = (13, "permission denied")
INTERNAL_ERROR_LINE = "MEM01 INTERNAL ERROR: CancelledError"
CLOSE_FAILURES = [
    pytest.param(OSError(*NO_SPACE), "OSError", id="OSError"),
    pytest.param(PermissionError(*DENIED), "PermissionError", id="PermissionError"),
]


class _FlushFailingStream:
    """A log stream whose writes succeed and whose `flush()` and `close()` raise `failure`."""

    def __init__(self, failure: BaseException) -> None:
        self._failure = failure
        self.written = 0

    def write(self, text: str) -> int:
        self.written += len(text)
        return len(text)

    def flush(self) -> None:
        raise self._failure

    def close(self) -> None:
        raise self._failure


def _park_a_real_capture(
    instrument: InstrumentLoader, state: object, report_dir: Path
) -> contextlib.ExitStack:
    """Open the real capture on `report_dir`; park it on `state.log_capture` as the runner does."""
    stack = contextlib.ExitStack()
    stack.enter_context(instrument("runner_logging").capture_app_logging(report_dir))
    state.log_capture = stack  # type: ignore[attr-defined]
    return stack


def _fail_the_close(failure: BaseException) -> object:
    """Replace the `app` handler's stream; return the real file object it displaces."""
    handler = harness.app_handler()
    displaced = handler.stream  # type: ignore[attr-defined]  # FileHandler
    handler.stream = _FlushFailingStream(failure)  # type: ignore[attr-defined]
    return displaced


def _bare_state(instrument: InstrumentLoader) -> object:
    return instrument("runner_output").RunState(
        run_kind="tuning",
        run_id=reference.oracle_run_id(71),
        started_at=datetime.now(UTC),
        partial=False,
        baseline_label=None,
    )


# ── (d) the unit: close_app_log returns the class and never raises ──────────────────────


@pytest.mark.parametrize(("failure", "class_name"), CLOSE_FAILURES)
def test_close_app_log_returns_the_failing_class_name_and_does_not_raise(
    instrument: InstrumentLoader, tmp_path: Path, failure: BaseException, class_name: str
) -> None:
    runner_logging = instrument("runner_logging")
    state = _bare_state(instrument)
    _park_a_real_capture(instrument, state, tmp_path / "report")
    displaced = _fail_the_close(failure)

    try:
        returned = runner_logging.close_app_log(state)
    except Exception as escaped:
        pytest.fail(f"close_app_log raised {type(escaped).__name__}")
    finally:
        displaced.close()  # type: ignore[attr-defined]

    assert returned == class_name
    assert state.log_capture is None  # type: ignore[attr-defined]
    assert runner_logging.close_app_log(state) is None  # idempotent: a repeat closes nothing


def test_close_app_log_returns_none_on_a_clean_close_and_again_on_a_repeat(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    runner_logging = instrument("runner_logging")
    state = _bare_state(instrument)
    _park_a_real_capture(instrument, state, tmp_path / "report")

    first = runner_logging.close_app_log(state)
    second = runner_logging.close_app_log(state)

    assert first is None and second is None  # positive control
    assert state.log_capture is None  # type: ignore[attr-defined]
    assert (tmp_path / "report" / "app.log").exists()


# ── (d) the runner: the close failure is folded at step 11 like an emit failure ─────────


async def _drive_with_a_failing_close(
    instrument: InstrumentLoader, criteria_path: Path, tmp_path: Path, failure: BaseException
) -> tuple[int | None, str, BaseException | None]:
    """Park the real capture, make its close fail, run steps 10-13; return any escape."""
    release_dir = tmp_path / "release"
    state = harness.completed_run_state(instrument, criteria_path, release_dir)
    stack = _park_a_real_capture(instrument, state, state.report_dir)  # type: ignore[attr-defined]
    displaced = _fail_the_close(failure)
    try:
        code, transcript = await harness.drive_finish(
            instrument, state, ["--release", str(release_dir)], release_dir / "absent-hidden"
        )
    except Exception as escaped:
        return None, "", escaped
    finally:
        displaced.close()  # type: ignore[attr-defined]
        stack.close()  # a no-op once the runner closed it
    return code, transcript, None


@pytest.mark.parametrize(("failure", "class_name"), CLOSE_FAILURES)
async def test_an_os_error_family_close_failure_aborts_the_run_at_step_11_as_artifacts_unwritable(
    instrument: InstrumentLoader,
    criteria_path: Path,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    failure: BaseException,
    class_name: str,
) -> None:
    capfd.readouterr()

    code, transcript, escaped = await _drive_with_a_failing_close(
        instrument, criteria_path, tmp_path, failure
    )
    captured = capfd.readouterr()

    assert escaped is None, f"the close failure escaped _finish as {type(escaped).__name__}"
    block = reference.extract_machine_block(transcript)
    assert block["aborted"] is True, "the close failure did not abort the run"
    assert block["aborted_at_step"] == harness.CLEANUP_STEP
    assert str(block["reason"]).startswith(f"artifacts_unwritable: app_log {class_name}")
    assert harness.verdict_lines(transcript) == []
    assert code == 2
    assert "Traceback" not in captured.out + captured.err


async def test_a_non_os_error_close_failure_is_evidence_only_and_the_run_completes(
    instrument: InstrumentLoader,
    criteria_path: Path,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    capfd.readouterr()

    code, transcript, escaped = await _drive_with_a_failing_close(
        instrument, criteria_path, tmp_path, RuntimeError("oracle: flush refused")
    )
    captured = capfd.readouterr()

    assert escaped is None, f"the close failure escaped _finish as {type(escaped).__name__}"
    block = reference.extract_machine_block(transcript)
    assert block["aborted"] is False
    assert block["diagnostics"]["run"]["app_log_emit_failures"] == ["RuntimeError"]
    assert len(harness.verdict_lines(transcript)) == 1
    assert code == 2  # the hand-built run's gates are all incomplete: ERROR, exit 2
    assert "Traceback" not in captured.out + captured.err


# ── (e) a cancellation outside the sequence ─────────────────────────────────────────────


def _drive_main(verify_step1: object, argv: list[str]) -> tuple[int | None, BaseException | None]:
    """Call `main(argv)`; an escaping BaseException is returned, never re-raised into pytest."""
    try:
        return verify_step1.main(argv), None  # type: ignore[attr-defined]
    except BaseException as escaped:  # the seal asserts nothing escapes, CancelledError included
        return None, escaped


def test_a_cancelled_error_outside_the_sequence_prints_the_internal_error_line(
    instrument: InstrumentLoader, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    verify_step1 = instrument("verify_step1")

    def cancelled(argv: list[str] | None) -> int:
        raise asyncio.CancelledError

    monkeypatch.setattr(verify_step1, "_guarded_main", cancelled)
    capfd.readouterr()

    code, escaped = _drive_main(verify_step1, [])
    captured = capfd.readouterr()

    assert escaped is None, f"{type(escaped).__name__} escaped main()"
    assert code == 2
    assert [line for line in captured.out.splitlines() if line.strip()] == [INTERNAL_ERROR_LINE]
    assert "Traceback" not in captured.err
