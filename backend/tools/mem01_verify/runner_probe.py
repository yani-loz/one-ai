"""
Role: WHERE a run writes and WHICH database it borrows — the §0 gold and hidden roots, the
      §3.1/§16.16(i) report directory and the report root its `protected_result_path` is
      recorded against, and the §3.2 step-5 probe half: the §12 preflight and the probe lease a
      fixture gate needs.
Used by: `tools.mem01_verify.runner_steps` (which composes the step sequence) and
      `.verify_step1`; sealed through the CLI by `tests/tools/mem01_verify/test_verify_step1_*.py`
      (the report-directory layout) and `test_verify_step1_probe_reuse.py` (the lease).
Depends on: `tools.mem01_verify.probe_db`, `.gates.registry` (`probe_gates`), `.exceptions`,
      `.runner_output` (`HIDDEN_RUN_KINDS`); `RunState` under `TYPE_CHECKING` only.
Key invariants:
  - A hidden run's report directory lies under the HIDDEN root and a tuning run's under the
    release (or under `--report-dir`); nothing here creates a directory, so §16.14's "no tree
    under an absent hidden root" is the caller's `artifacts_writable` check, not a side effect.
  - §16.16(t): the Alembic child's `migration_log` is passed ONLY when the caller says the report
    directory may be written.
  - The stale-probe listing runs on every run; the §12 preflight only when a selected gate needs
    a database (§16.16(n)). Nothing here prints.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from tools.mem01_verify import probe_db
from tools.mem01_verify.exceptions import ProbeDatabaseError
from tools.mem01_verify.gates.registry import probe_gates
from tools.mem01_verify.runner_output import HIDDEN_RUN_KINDS

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tools.mem01_verify.runner_output import RunState

RELEASES_DIRNAME = "releases"
REPORTS_DIRNAME = "reports"
PROBE_MIGRATION_LOG_NAME = "probe_migration.log"

#: The workspace root — the PARENT of `backend/`, which is what §0 measures the gold root
#: from and what `run_identity`'s `backend/`-prefixed scopes are relative to.
REPO_ROOT = Path(__file__).resolve().parents[3]


def root_from_env(kind: str) -> Path:
    """Return the gold (`kind="gold"`) or hidden (`kind="hidden"`) root of contract §0."""
    configured = os.environ.get(f"MEM01_{kind.upper()}_ROOT")
    if configured:
        return Path(configured)
    return REPO_ROOT.parent / "Benchmarks" / f"_mem01_{kind.lower()}"


def resolve_release_dir(release_option: str | None, default_name: str) -> Path:
    """The release this run verifies (§3.1): `--release`, else the default under the gold root."""
    if release_option:
        return Path(release_option)
    return root_from_env("gold") / RELEASES_DIRNAME / default_name


def assign_report_paths(
    state: RunState, *, report_dir_option: str | None, hidden_root: Path
) -> None:
    """Step 3: pin where this run writes and the root §16.16(i) records its paths against."""
    state.report_dir = resolve_report_dir(
        state, report_dir_option=report_dir_option, hidden_root=hidden_root
    )
    state.report_root = resolve_report_root(
        state, report_dir_option=report_dir_option, hidden_root=hidden_root
    )


def resolve_report_dir(
    state: RunState, *, report_dir_option: str | None, hidden_root: Path
) -> Path:
    """Where this run's artifacts go (§3.1): under the hidden root for a hidden run."""
    release = state.require_release()
    if state.run_kind in HIDDEN_RUN_KINDS:
        return hidden_root / RELEASES_DIRNAME / release.name / REPORTS_DIRNAME / state.run_id
    if report_dir_option:
        return Path(report_dir_option) / state.run_id
    return release.path / REPORTS_DIRNAME / state.run_id


def resolve_report_root(
    state: RunState, *, report_dir_option: str | None, hidden_root: Path
) -> Path:
    """The root the recorded `protected_result_path` is relative to (§16.16(i)).

    The hidden root on a hidden run, the `--report-dir` directory when that option was given,
    and otherwise the release directory — the three roots §16.16(i) names, in that order.
    """
    if state.run_kind in HIDDEN_RUN_KINDS:
        return hidden_root
    if report_dir_option:
        return Path(report_dir_option)
    return state.require_release().path


async def acquire_probe(
    state: RunState,
    *,
    selected: frozenset[str] | None,
    probe_db_name: str | None,
    keep: bool,
    report_writable: bool,
) -> object | None:
    """Step 5 (probe half): preflight the server and lease a probe when a fixture gate needs one.

    `report_writable` says whether this run may write its report directory: a failing Alembic
    child's output reaches `<report dir>/probe_migration.log` only then, because §16.14 forbids
    a tree under an absent hidden root (§16.16(t)).

    Returns:
        The open probe context manager (the caller closes it after the drop), or None when no
        selected gate needs a database.

    Raises:
        ProbeDatabaseError: the preflight failed, the `--probe-db` target is not a legitimate
            free probe, or creation / migration failed (§12).
        IntegrityViolationError: a gate module declares no `NEEDS_PROBE`, so the set of gates
            needing a probe cannot be established.
    """
    state.stale_probes = tuple(await probe_db.list_stale_probe_databases())
    if not (selected is None or selected & probe_gates()):
        return None
    preflight = await probe_db.preflight_probe_server()
    refused = sorted(role for role, ok in preflight.login_ok.items() if not ok)
    if not preflight.roles_ok or refused:
        raise ProbeDatabaseError(
            f"the configured server cannot host a probe: missing roles "
            f"{list(preflight.missing_roles)}, roles refusing to log in {refused}"
        )
    if not preflight.can_create_database:
        raise ProbeDatabaseError("the configured owner role may not create databases")
    migration_log = (
        state.require_report_dir() / PROBE_MIGRATION_LOG_NAME if report_writable else None
    )
    manager = (
        probe_db.claim_probe_database(probe_db_name, state.run_id)
        if probe_db_name
        else probe_db.create_probe_database(state.run_id, keep=keep, migration_log=migration_log)
    )
    probe = await manager.__aenter__()  # type: ignore[attr-defined]  # both are async CMs
    state.probe = probe
    state.probe_name = probe.name
    state.probe_kept = keep
    return manager
