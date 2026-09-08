"""
Role: Seals fix-registry row A1(b) — `InputObserver.check_within` never reports a path under
      `backend/.venv/` or inside a `__pycache__` directory as an offender (their content is
      pinned through `uv.lock` in `code_hash`), while a repository file outside the closure is
      still reported and an editable-scope file never is; the runner's observer-suspension
      seam (`runner_steps.declared_boundary`) no longer exists.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.run_identity, .criteria, .corpus_identity, .runner_steps
      (imported inside each test); tests.tools.mem01_verify.conftest (criteria_path).
Key invariants:
  - The synthetic repository is built from scratch in tmp_path with a `.venv` tree, two
    `__pycache__` trees, an editable file and a `scripts/` file; the real repository is never
    observed.
  - Offender lists are compared by path suffix with forward slashes (platform-neutral).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from tests.tools.mem01_verify.conftest import InstrumentLoader

HEX = "0" * 64
EDITABLE_FILE = "backend/app/a.py"
SCRIPTS_FILE = "backend/scripts/foo.py"
VENV_METADATA = "backend/.venv/Lib/site-packages/x/METADATA"
APP_PYCACHE = "backend/app/__pycache__/a.cpython-312.pyc"
SCRIPTS_PYCACHE = "backend/scripts/__pycache__/foo.cpython-312.pyc"
REPO_FILES = {
    EDITABLE_FILE: b"print('a')\n",
    SCRIPTS_FILE: b"print('outside the closure')\n",
    VENV_METADATA: b"Metadata-Version: 2.1\nName: x\n",
    APP_PYCACHE: b"\x00bytecode of a.py",
    SCRIPTS_PYCACHE: b"\x00bytecode of foo.py",
    "backend/pyproject.toml": b"[project]\nname='x'\n",
    "backend/uv.lock": b"version = 1\n",
    "backend/.python-version": b"3.12\n",
    "backend/alembic.ini": b"[alembic]\n",
    "backend/.env": b"POSTGRES_HOST=localhost\n",
}


def _synthetic_repo(root: Path) -> Path:
    for relative, payload in REPO_FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return root


def _closure(instrument: InstrumentLoader, repo: Path, criteria_path: Path) -> object:
    corpus_identity = instrument("corpus_identity")
    corpus = corpus_identity.CorpusIdentity(
        version="CORPUS_DIGEST_V1",
        corpus_digest=HEX,
        text_digest="1" * 64,
        roster_counts={"email_message": 6},
        taken_at=datetime.now(UTC),
        snapshot_transaction_id="00000A1B-1",
        database="mem01_probe_oracle",
        host="localhost",
        port=5432,
        org_id=uuid4(),
    )
    return instrument("run_identity").build_closure(
        repo,
        instrument("criteria").load_criteria(criteria_path),
        corpus=corpus,
        migrations_digest=HEX,
        fixtures_digest=HEX,
        cli_options={"gates": None},
    )


def _posix(items: Iterable[object]) -> list[str]:
    return [str(item).replace("\\", "/") for item in items]


def test_venv_and_pycache_paths_are_never_offenders_while_a_scripts_file_still_is(
    instrument: InstrumentLoader, criteria_path: Path, tmp_path: Path
) -> None:
    run_identity = instrument("run_identity")
    repo = _synthetic_repo(tmp_path / "repo")
    closure = _closure(instrument, repo, criteria_path)

    with run_identity.InputObserver(repo) as observer:
        for relative in (VENV_METADATA, APP_PYCACHE, SCRIPTS_FILE, EDITABLE_FILE):
            (repo / relative).read_bytes()
    offenders = _posix(observer.check_within(closure))
    observed = _posix(observer.observed_paths)

    assert any(item.endswith(SCRIPTS_FILE) for item in offenders)  # positive control
    assert any(item.endswith(SCRIPTS_FILE) for item in observed)
    assert not any("/.venv/" in item for item in offenders)
    assert not any("__pycache__" in item for item in offenders)
    assert not any(item.endswith(EDITABLE_FILE) for item in offenders)


def test_a_pycache_directory_outside_the_editable_scope_is_still_not_an_offender(
    instrument: InstrumentLoader, criteria_path: Path, tmp_path: Path
) -> None:
    run_identity = instrument("run_identity")
    repo = _synthetic_repo(tmp_path / "repo")
    closure = _closure(instrument, repo, criteria_path)

    with run_identity.InputObserver(repo) as observer:
        (repo / SCRIPTS_PYCACHE).read_bytes()
        (repo / SCRIPTS_FILE).read_bytes()
    offenders = _posix(observer.check_within(closure))

    assert [item for item in offenders if item.endswith("foo.cpython-312.pyc")] == []
    assert any(item.endswith(SCRIPTS_FILE) for item in offenders)  # the sibling source still is


def test_runner_steps_no_longer_exposes_the_observer_suspension_seam(
    instrument: InstrumentLoader,
) -> None:
    runner_steps = instrument("runner_steps")

    assert not hasattr(runner_steps, "declared_boundary")
