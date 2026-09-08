"""
Role: Seals fix-registry row A30 / contract §16.17(d) on the capture handlers — an app-log emit
      failure never reaches a stream: the capture handler (file or discard sink) records the
      failing exception's CLASS name only — the most specific BUILTIN class in its MRO, so a
      vendor `PermissionError` subclass records as `PermissionError`, exercised in its OWN
      capture so a handler that ignores non-builtin exceptions fails on it alone — and
      `runner_logging.app_log_emit_failures()` reports the sorted distinct classes while the
      capture is open and nothing once it has closed. The runner-level folding at step 11 is
      sealed by `test_review_round_5_logging_b.py`.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.runner_output, .runner_logging (imported inside each test through
      the `instrument` loader); tests.tools.mem01_verify.review_round_5_harness (the stream
      breaker) and .reference; pytest capfd.
Key invariants:
  - Markers are unique nonce strings, never personal data; every marker reaches the handler
    ONLY through `args`, so the stock `handleError` would print it on stderr under `Arguments:`.
  - The real file object the broken stream displaces is closed by the test itself.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify import review_round_5_harness as harness
from tests.tools.mem01_verify.conftest import InstrumentLoader

MARKER_OS = "oracle-round5-no-space-marker-91ab"
MARKER_TYPE = "oracle-round5-too-few-args-2d7c"
MARKER_SINK = "oracle-round5-sink-marker-6e30"
MARKER_CONTROL = "oracle-round5-inside-control-b7f4"
NO_SPACE = (28, "no space left on device")


class _OracleVendorError(PermissionError):
    """A third-party subclass of a builtin OSError family member."""


def _streams(captured: object) -> str:
    return captured.out + captured.err  # type: ignore[attr-defined]


def test_an_os_error_while_emitting_reaches_neither_stream(
    instrument: InstrumentLoader, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    runner_output = instrument("runner_output")
    capfd.readouterr()

    with runner_output.capture_app_logging(tmp_path):
        displaced = harness.break_app_log_stream(OSError(*NO_SPACE))
        logging.getLogger("app.round5").warning("%s", MARKER_OS)
    displaced.close()  # type: ignore[attr-defined]
    captured = capfd.readouterr()

    assert MARKER_OS not in _streams(captured)
    assert "Traceback" not in _streams(captured)


def test_an_os_error_while_emitting_is_recorded_by_class_and_cleared_when_the_capture_closes(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    runner_output = instrument("runner_output")
    runner_logging = instrument("runner_logging")

    with runner_output.capture_app_logging(tmp_path):
        displaced = harness.break_app_log_stream(OSError(*NO_SPACE))
        logging.getLogger("app.round5").warning("%s", MARKER_OS)
        inside = runner_logging.app_log_emit_failures()
    displaced.close()  # type: ignore[attr-defined]

    assert inside == ("OSError",)
    assert runner_logging.app_log_emit_failures() == ()


def test_a_permission_error_while_emitting_is_recorded_by_its_own_class(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    runner_output = instrument("runner_output")
    runner_logging = instrument("runner_logging")

    with runner_output.capture_app_logging(tmp_path):
        displaced = harness.break_app_log_stream(PermissionError(13, "denied"))
        logging.getLogger("app.round5").warning("%s", MARKER_OS)
        inside = runner_logging.app_log_emit_failures()
    displaced.close()  # type: ignore[attr-defined]

    assert inside == ("PermissionError",)  # the most specific builtin class, never plain OSError


def test_a_vendor_permission_error_subclass_is_recorded_as_its_builtin_base_in_its_own_capture(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    runner_output = instrument("runner_output")
    runner_logging = instrument("runner_logging")

    with runner_output.capture_app_logging(tmp_path):
        displaced = harness.break_app_log_stream(_OracleVendorError(13, "vendor denied"))
        logging.getLogger("app.round5").warning("%s", MARKER_OS)
        inside = runner_logging.app_log_emit_failures()
    displaced.close()  # type: ignore[attr-defined]

    assert inside == ("PermissionError",)  # a non-builtin class is folded, never ignored


def test_a_formatting_type_error_reaches_neither_stream(
    instrument: InstrumentLoader, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    runner_output = instrument("runner_output")
    capfd.readouterr()

    with runner_output.capture_app_logging(tmp_path):
        logging.getLogger("app.round5").warning("%s %s", MARKER_TYPE)  # too few args
    captured = capfd.readouterr()

    assert MARKER_TYPE not in _streams(captured)
    assert "Traceback" not in _streams(captured)


def test_a_formatting_type_error_is_recorded_by_class_and_cleared_when_the_capture_closes(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    runner_output = instrument("runner_output")
    runner_logging = instrument("runner_logging")

    with runner_output.capture_app_logging(tmp_path):
        logging.getLogger("app.round5").warning("%s %s", MARKER_TYPE)  # too few args
        inside = runner_logging.app_log_emit_failures()

    assert inside == ("TypeError",)
    assert runner_logging.app_log_emit_failures() == ()


def _sink_record() -> logging.LogRecord:
    return logging.LogRecord("app.round5", logging.WARNING, __file__, 0, "%s", (MARKER_SINK,), None)


def test_the_discard_sink_handles_an_emit_error_without_a_stream(
    instrument: InstrumentLoader, capfd: pytest.CaptureFixture[str]
) -> None:
    runner_logging = instrument("runner_logging")
    capfd.readouterr()

    with runner_logging.discard_app_logging():
        sink = harness.app_handler()
        try:
            raise OSError(*NO_SPACE)
        except OSError:
            sink.handleError(_sink_record())
    captured = capfd.readouterr()

    assert MARKER_SINK not in _streams(captured)
    assert "Traceback" not in _streams(captured)


def test_the_discard_sink_records_the_emit_error_class(instrument: InstrumentLoader) -> None:
    runner_logging = instrument("runner_logging")

    with runner_logging.discard_app_logging():
        sink = harness.app_handler()
        try:
            raise OSError(*NO_SPACE)
        except OSError:
            sink.handleError(_sink_record())
        inside = runner_logging.app_log_emit_failures()

    assert inside == ("OSError",)
    assert runner_logging.app_log_emit_failures() == ()


def test_a_record_inside_the_capture_still_reaches_app_log_and_no_stream(
    instrument: InstrumentLoader, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    runner_output = instrument("runner_output")
    capfd.readouterr()

    with runner_output.capture_app_logging(tmp_path):
        logging.getLogger("app.round5").warning("%s", MARKER_CONTROL)
    captured = capfd.readouterr()

    assert MARKER_CONTROL in reference.read_text(tmp_path / "app.log")  # positive control
    assert MARKER_CONTROL not in _streams(captured)
