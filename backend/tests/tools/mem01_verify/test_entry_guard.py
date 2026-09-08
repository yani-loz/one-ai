"""
Role: Seals fix-registry row A25 / contract §16.16(t) (R11) — the runner's ONE entry guard: a
      configuration error raised while the settings are constructed (before any argument is
      parsed and before any database is reached) ends the process with exit 2 and the single
      stdout line `MEM01 INTERNAL ERROR: <class>`, never a traceback on stderr, in the §3.1
      script form and the module form alike: `POSTGRES_PORT=not_a_number` → `ValidationError`,
      `APP_ENV=production` → `InsecureConfigurationError`.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: conftest.run_cli (the child environment; `extra_env` overrides win) and
      tests.tools.mem01_verify.reference (strict UTF-8 decoding of both streams).
Key invariants:
  - The child's configured database is an absent probe name and, for the `APP_ENV` case, the
    port is unreachable as well, so a run that reached the server would abort for a different
    reason than the sealed class name; the release path is an empty temporary directory.
  - `run_subprocess` decodes stdout and stderr as STRICT UTF-8, so a non-UTF-8 byte on either
    stream fails the test by itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import SESSION_LOOP, CliRunner
from tests.tools.mem01_verify.reference import CliRun

ABSENT_DATABASE = "mem01_probe_absent_oracle_00000000"  # never created
UNREACHABLE_PORT = "1"
INTERNAL_ERROR_LINE = "MEM01 INTERNAL ERROR: {cls}"


def _assert_entry_guard(run: CliRun, cls: str) -> None:
    assert run.exit_code == 2, (run.exit_code, run.stderr[-1500:])
    assert reference.last_nonempty_line(run.stdout) == INTERNAL_ERROR_LINE.format(cls=cls)
    assert "Traceback" not in run.stderr, run.stderr[-1500:]
    assert "MEM01_RESULT_V1_BEGIN" not in run.stdout  # no block reaches stdout
    assert not any(line.startswith("STEP1 ") for line in run.stdout.splitlines())


def _release_dir(tmp_path: Path) -> Path:
    release = tmp_path / "release"
    release.mkdir()
    return release


@SESSION_LOOP
@pytest.mark.parametrize("form", ["script", "module"])
async def test_an_invalid_port_setting_dies_at_the_entry_guard_with_validation_error(
    run_cli: CliRunner, tmp_path: Path, form: str
) -> None:
    release = _release_dir(tmp_path)

    run = await run_cli(
        ["--release", str(release)],
        database=ABSENT_DATABASE,
        gold_root=tmp_path,
        form=form,  # type: ignore[arg-type]
        extra_env={"POSTGRES_PORT": "not_a_number"},
    )

    _assert_entry_guard(run, "ValidationError")


@SESSION_LOOP
async def test_a_production_app_env_dies_at_the_entry_guard_with_insecure_configuration_error(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    release = _release_dir(tmp_path)

    run = await run_cli(
        ["--release", str(release)],
        database=ABSENT_DATABASE,
        gold_root=tmp_path,
        form="script",
        extra_env={"APP_ENV": "production", "POSTGRES_PORT": UNREACHABLE_PORT},
    )

    _assert_entry_guard(run, "InsecureConfigurationError")
