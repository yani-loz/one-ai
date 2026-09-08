"""
Role: Seals the UTF-8 self-test of contract §3.9 / R8 at module level — the exact lines, the
      stream reconfiguration to UTF-8 regardless of its starting encoding, and the wrapped
      failure (Utf8SelfTestError, never a raw OSError). The ordering rule (self-test before any
      other output) is sealed on the CLI in test_verify_step1_cli.py.
Used by: the seal review.
Depends on: tools.mem01_verify.utf8_selftest and .exceptions (imported inside each test).
Key invariants:
  - The stream under test starts as cp1252 on purpose — the Windows console code page that
    breaks Cyrillic — so a self-test that merely writes without reconfiguring goes red.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from tests.tools.mem01_verify.conftest import InstrumentLoader

EXPECTED_LINE = "MEM01 UTF-8 self-test: Здравей, свят — кирилица OK"
EXPECTED_FAILED_LINE = "MEM01 UTF-8 self-test FAILED"


class _BrokenStream(io.TextIOBase):
    """A text stream whose every write fails (an unwritable console)."""

    def write(self, text: str) -> int:  # noqa: D102 - behaviour is the point
        raise OSError("console gone")

    def reconfigure(self, **kwargs: object) -> None:  # noqa: D102
        return None


def test_selftest_constants_are_the_exact_contract_lines(instrument: InstrumentLoader) -> None:
    utf8_selftest = instrument("utf8_selftest")

    assert utf8_selftest.UTF8_SELFTEST_LINE == EXPECTED_LINE
    assert utf8_selftest.UTF8_SELFTEST_FAILED_LINE == EXPECTED_FAILED_LINE


def test_selftest_writes_the_line_as_utf8_on_a_cp1252_stream(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    utf8_selftest = instrument("utf8_selftest")
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict", newline="\n")

    utf8_selftest.run_utf8_selftest(stream, tmp_path)
    stream.flush()

    assert raw.getvalue().decode("utf-8") == EXPECTED_LINE + "\n"
    assert stream.encoding.lower().replace("-", "") == "utf8"


def test_selftest_raises_utf8_error_when_the_scratch_dir_is_a_file(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    utf8_selftest = instrument("utf8_selftest")
    exceptions = instrument("exceptions")
    not_a_directory = tmp_path / "scratch"
    not_a_directory.write_bytes(b"")
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline="\n")

    with pytest.raises(exceptions.Utf8SelfTestError):
        utf8_selftest.run_utf8_selftest(stream, not_a_directory)
    # positive control: a real directory passes
    utf8_selftest.run_utf8_selftest(stream, tmp_path)


def test_selftest_wraps_a_failing_stream_into_utf8_error(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    utf8_selftest = instrument("utf8_selftest")
    exceptions = instrument("exceptions")

    with pytest.raises(exceptions.Utf8SelfTestError):
        utf8_selftest.run_utf8_selftest(_BrokenStream(), tmp_path)


def test_utf8_selftest_error_is_a_mem01_error(instrument: InstrumentLoader) -> None:
    exceptions = instrument("exceptions")

    assert issubclass(exceptions.Utf8SelfTestError, exceptions.Mem01Error)
