"""
Role: Seals fix-registry row A7 — `runner_output.protected_result_relpath(report_dir,
      report_root)` is the posix path of `<report_dir>/protected_result.json` relative to the
      report root: `releases/<name>/reports/<run_id>/protected_result.json` under a hidden root,
      `reports/<run_id>/protected_result.json` under a release directory, never absolute, never
      with a backslash; a report directory outside the root (including a sibling whose name
      merely shares the root's prefix) is refused with `IntegrityViolationError`.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.runner_output, .exceptions (imported inside each test);
      tests.tools.mem01_verify.reference (run-id forms).
Key invariants:
  - Every directory is created in tmp_path so an implementation that resolves paths on disk and
    one that computes them lexically must agree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import InstrumentLoader

RUN_ID = reference.oracle_run_id(8)
NAME = "step1-gold-v1"


def _made(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_relpath_under_a_hidden_root_names_the_release_reports_and_run_id(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    runner_output = instrument("runner_output")
    hidden_root = _made(tmp_path / "hidden")
    report_dir = _made(hidden_root / "releases" / NAME / "reports" / RUN_ID)

    relative = runner_output.protected_result_relpath(report_dir, hidden_root)

    assert relative == f"releases/{NAME}/reports/{RUN_ID}/protected_result.json"


def test_relpath_under_a_release_dir_is_reports_run_id_protected_result(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    runner_output = instrument("runner_output")
    release = _made(tmp_path / "gold" / "releases" / NAME)
    report_dir = _made(release / "reports" / RUN_ID)

    relative = runner_output.protected_result_relpath(report_dir, release)

    assert relative == f"reports/{RUN_ID}/protected_result.json"
    assert isinstance(relative, str) and "\\" not in relative
    assert not Path(relative).is_absolute()


def test_relpath_refuses_a_report_dir_outside_the_root_including_a_prefix_sibling(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    runner_output = instrument("runner_output")
    exceptions = instrument("exceptions")
    release = _made(tmp_path / "gold" / "releases" / NAME)
    elsewhere = _made(tmp_path / "elsewhere" / "reports" / RUN_ID)
    prefix_sibling = _made(tmp_path / "gold" / "releases" / f"{NAME}0" / "reports" / RUN_ID)

    with pytest.raises(exceptions.IntegrityViolationError):
        runner_output.protected_result_relpath(elsewhere, release)
    with pytest.raises(exceptions.IntegrityViolationError):
        runner_output.protected_result_relpath(prefix_sibling, release)
    inside = runner_output.protected_result_relpath(_made(release / "reports" / RUN_ID), release)

    assert inside == f"reports/{RUN_ID}/protected_result.json"  # positive control
