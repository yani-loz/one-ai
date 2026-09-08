"""
Role: Seals fix-registry row A9 — inside `runner_output.capture_app_logging(report_dir)` every
      record of the `app` logger hierarchy is written to `<report_dir>/app.log` and reaches no
      stream (neither stdout/stderr at the descriptor level nor a stream handler installed
      before the context, or a descendant logger's own handler with `propagate=False`); a
      record of another hierarchy never enters `app.log`; after the
      context (normal exit or an exception) the handlers and propagation are restored, so the
      same warning reaches stderr again.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.runner_output (imported inside each test); pytest capfd.
Key invariants:
  - Markers are unique nonce strings, never personal data; the stream probe is a plain
    `logging.StreamHandler` on the root logger that the test itself removes in `finally`.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from tests.tools.mem01_verify.conftest import InstrumentLoader

MARKER_INSIDE = "oracle-app-record-inside-7f3a"
MARKER_OUTSIDE = "oracle-app-record-outside-9c1d"
MARKER_FOREIGN = "oracle-foreign-record-2b8e"
MARKER_RAISED = "oracle-app-record-before-raise-4e6f"
MARKER_DESCENDANT = "oracle-app-descendant-record-5a7b"
MARKER_DESCENDANT_AFTER = "oracle-app-descendant-after-8d2c"


class _OracleAbortError(RuntimeError):
    """Raised inside the context to prove the restore happens on the exception path too."""


def _snapshot() -> tuple[list[logging.Handler], list[logging.Handler], bool, int]:
    root, app = logging.getLogger(), logging.getLogger("app")
    return list(root.handlers), list(app.handlers), app.propagate, app.level


def test_app_records_inside_the_context_reach_app_log_and_no_stream(
    instrument: InstrumentLoader, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    runner_output = instrument("runner_output")
    root = logging.getLogger()
    probe = logging.StreamHandler()  # sys.stderr as captured by capfd
    probe.setLevel(logging.WARNING)
    root.addHandler(probe)
    try:
        before = _snapshot()
        capfd.readouterr()

        with runner_output.capture_app_logging(tmp_path):
            logging.getLogger("app.test").warning("%s", MARKER_INSIDE)
            logging.getLogger("oracle.other").warning("%s", MARKER_FOREIGN)
            captured_inside = capfd.readouterr()
        logging.getLogger("app.test").warning("%s", MARKER_OUTSIDE)
        captured_after = capfd.readouterr()
        after = _snapshot()
    finally:
        root.removeHandler(probe)

    log_text = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert MARKER_INSIDE in log_text
    assert MARKER_OUTSIDE not in log_text and MARKER_FOREIGN not in log_text
    assert MARKER_INSIDE not in captured_inside.out + captured_inside.err
    assert MARKER_OUTSIDE in captured_after.err  # positive control: the stream probe is back
    assert after == before


def test_handlers_are_restored_when_the_context_exits_through_an_exception(
    instrument: InstrumentLoader, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    runner_output = instrument("runner_output")
    root = logging.getLogger()
    probe = logging.StreamHandler()
    probe.setLevel(logging.WARNING)
    root.addHandler(probe)
    try:
        before = _snapshot()
        capfd.readouterr()

        with pytest.raises(_OracleAbortError):
            with runner_output.capture_app_logging(tmp_path):
                logging.getLogger("app.test").warning("%s", MARKER_RAISED)
                raise _OracleAbortError("interrupted between steps 4 and 11")
        captured_raise = capfd.readouterr()
        logging.getLogger("app.test").warning("%s", MARKER_OUTSIDE)
        captured_after = capfd.readouterr()
        after = _snapshot()
    finally:
        root.removeHandler(probe)

    assert MARKER_RAISED in (tmp_path / "app.log").read_text(encoding="utf-8")
    assert MARKER_RAISED not in captured_raise.out + captured_raise.err
    assert MARKER_OUTSIDE in captured_after.err
    assert after == before


def test_a_descendant_logger_with_its_own_stream_handler_is_captured_and_restored(
    instrument: InstrumentLoader, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    runner_output = instrument("runner_output")
    descendant = logging.getLogger("app.connectors.x")
    own = logging.StreamHandler()  # sys.stderr as captured by capfd
    own.setLevel(logging.WARNING)
    descendant.addHandler(own)
    propagate_before = descendant.propagate
    descendant.propagate = False
    try:
        handlers_before = list(descendant.handlers)
        capfd.readouterr()

        with runner_output.capture_app_logging(tmp_path):
            descendant.warning("%s", MARKER_DESCENDANT)
            captured_inside = capfd.readouterr()
        handlers_after = list(descendant.handlers)
        propagate_after = descendant.propagate
        descendant.warning("%s", MARKER_DESCENDANT_AFTER)
        captured_after = capfd.readouterr()
    finally:
        descendant.removeHandler(own)
        descendant.propagate = propagate_before

    log_text = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert MARKER_DESCENDANT in log_text and MARKER_DESCENDANT_AFTER not in log_text
    assert MARKER_DESCENDANT not in captured_inside.out + captured_inside.err
    assert handlers_after == handlers_before and propagate_after is False  # restored on exit
    assert MARKER_DESCENDANT_AFTER in captured_after.err  # its own handler works again
