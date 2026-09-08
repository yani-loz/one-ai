"""
Role: Seals fix-registry row A6 — `validation_guard.seal_aborted_attempt(report_dir)` renames an
      aborted validation attempt's report directory to `<run_id>.sealed` with its files intact,
      refuses with `ValidationGuardError` when the target already exists (leaving source and
      target untouched), and never touches a sibling run directory.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.validation_guard, .exceptions (imported inside each test);
      tests.tools.mem01_verify.reference (run-id forms).
Key invariants:
  - The report tree is built by hand in tmp_path; the payloads are compared byte for byte after
    the rename so a copy-and-truncate or a partial move is visible.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import InstrumentLoader

RUN_ID = reference.oracle_run_id(5)
OTHER_RUN_ID = reference.oracle_run_id(6)
PROTECTED = b'{"aborted": true, "reason": "interrupted before the verdict"}\n'
STDOUT = "MEM01 UTF-8 self-test: Здравей, свят — кирилица OK\n".encode()
OTHER_PROTECTED = b'{"aborted": false}\n'


def _reports(tmp_path: Path) -> Path:
    reports = tmp_path / "reports"
    attempt = reports / RUN_ID
    attempt.mkdir(parents=True)
    (attempt / "protected_result.json").write_bytes(PROTECTED)
    (attempt / "stdout.txt").write_bytes(STDOUT)
    (attempt / "gates").mkdir()
    (attempt / "gates" / "QS.json").write_bytes(b"{}")
    other = reports / OTHER_RUN_ID
    other.mkdir()
    (other / "protected_result.json").write_bytes(OTHER_PROTECTED)
    return reports


def test_seal_aborted_attempt_renames_the_report_dir_and_keeps_every_file_byte_for_byte(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    guard = instrument("validation_guard")
    reports = _reports(tmp_path)

    sealed = guard.seal_aborted_attempt(reports / RUN_ID)

    assert Path(sealed) == reports / f"{RUN_ID}.sealed" and Path(sealed).is_dir()
    assert not (reports / RUN_ID).exists()
    assert (Path(sealed) / "protected_result.json").read_bytes() == PROTECTED
    assert (Path(sealed) / "stdout.txt").read_bytes() == STDOUT
    assert (Path(sealed) / "gates" / "QS.json").read_bytes() == b"{}"
    assert {path.name for path in reports.iterdir()} == {f"{RUN_ID}.sealed", OTHER_RUN_ID}
    assert (reports / OTHER_RUN_ID / "protected_result.json").read_bytes() == OTHER_PROTECTED


def test_seal_aborted_attempt_refuses_an_existing_target_and_leaves_both_directories_untouched(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    guard = instrument("validation_guard")
    exceptions = instrument("exceptions")
    reports = _reports(tmp_path)
    target = reports / f"{RUN_ID}.sealed"
    target.mkdir()
    (target / "marker.txt").write_bytes(b"an earlier sealed attempt")

    with pytest.raises(exceptions.ValidationGuardError):
        guard.seal_aborted_attempt(reports / RUN_ID)

    assert (reports / RUN_ID / "protected_result.json").read_bytes() == PROTECTED
    assert (reports / RUN_ID / "stdout.txt").read_bytes() == STDOUT
    assert {path.name for path in target.iterdir()} == {"marker.txt"}
    assert (reports / OTHER_RUN_ID / "protected_result.json").read_bytes() == OTHER_PROTECTED


def test_validation_guard_error_is_a_mem01_error(instrument: InstrumentLoader) -> None:
    exceptions = instrument("exceptions")

    assert issubclass(exceptions.ValidationGuardError, exceptions.Mem01Error)
