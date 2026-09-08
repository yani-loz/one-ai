"""
Role: The candidate's run identity of contract §3.11 — the `Closure` (editable-scope code files,
      declared config artifacts, env allowlist names, criteria / migrations / corpus / fixtures
      digests, library versions and CLI options), the `code_hash` and `config_hash` derived from
      it, the database-side `migrations_digest` and `db_versions`, the library `versions()`, and
      the §1.3 re-exports `new_run_id` (from `.run_id`) and `InputObserver` (from
      `.input_observer`, which holds the observer's audit-hook machinery).
Used by: tools.mem01_verify.verify_step1 (step 5 builds the closure and the two hashes, step 9
      checks the observer), .hidden_budget and .validation_guard (both key on the two hashes),
      .probe_env (`PACKAGED_CRITERIA_PATH`, the annex whose `env_allowlist` a child inherits),
      and the sealed oracle module tests/tools/mem01_verify/test_run_identity.py.
Depends on: tools.mem01_verify.criteria (`CriteriaFile`), .corpus_identity (`CorpusIdentity`),
      .hashing (`sha256_file`, `canonical_lines_digest`, `sha256_bytes`, `is_bytecode`),
      .run_id (`new_run_id`, re-exported), .input_observer (`InputObserver`, re-exported), and
      .db (`read_alembic_version`, imported inside `migrations_digest` so the module pair does
      not form an import cycle); sqlalchemy for the async readers, stdlib else.
Key invariants:
  - `code_hash` covers EXACTLY the editable scope (`backend/app/`, `backend/tests/mem01/`) plus
    the three dependency pins — never the runner folder, never docs, never bytecode (§16.12), so
    bytecode a run writes can never move the next run's hash. The scope walk excludes what
    `hashing.is_bytecode` names — the §16.16(r)/A26 suffix chain, CPython's temporary
    `x.cpython-312.pyc.140213` included — and NOTHING else: no directory is excluded, so a
    non-bytecode file under `__pycache__` inside the scope is hashed like any source.
  - `config_hash` covers the declared artifacts' bytes ONLY when `hashed: true`; a secret-bearing
    artifact enters as `None`, its bytes never reach the digest, and the env allowlist enters by
    NAME (values never do).
  - The observer this module re-exports is telemetry over the interpreter's own `open` events and
    environment reads (§3.11), never native or child-process reads; its exemptions (`.venv`,
    bytecode) and their justification live with it in `.input_observer`.
"""

from __future__ import annotations

import json
import platform
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path

from sqlalchemy import text

from tools.mem01_verify.corpus_identity import CorpusIdentity
from tools.mem01_verify.criteria import CriteriaFile, criteria_sha256
from tools.mem01_verify.hashing import (
    canonical_lines_digest,
    is_bytecode,
    sha256_bytes,
    sha256_file,
)

# §1.3 places both names on this module; their implementations live in the siblings named here.
from tools.mem01_verify.input_observer import InputObserver as InputObserver
from tools.mem01_verify.run_id import new_run_id as new_run_id

PACKAGED_CRITERIA_PATH = Path(__file__).resolve().parent / "release" / "criteria.step1.v1.yaml"
"""The annex the instrument ships — the closure's handle on its bytes (lock stage 1 re-checks)."""

EDITABLE_SCOPE: tuple[str, ...] = ("backend/app", "backend/tests/mem01")
"""The directories `code_hash` walks, repo-relative posix (§3.11)."""

DEPENDENCY_FILES: tuple[str, ...] = (
    "backend/pyproject.toml",
    "backend/uv.lock",
    "backend/.python-version",
)
"""The dependency and interpreter pins that join the editable scope in `code_hash`."""

RUNNER_FOLDER = "backend/tools/mem01_verify"
"""The instrument's own folder — excluded from `code_hash`, allowed to the input observer."""

LIBRARY_DISTRIBUTIONS: Mapping[str, str] = {
    "sqlalchemy": "SQLAlchemy",
    "asyncpg": "asyncpg",
    "charset_normalizer": "charset-normalizer",
    "html2text": "html2text",
    "striprtf": "striprtf",
    "pdfplumber": "pdfplumber",
    "pypdf": "pypdf",
    "python_docx": "python-docx",
    "openpyxl": "openpyxl",
    "tnefparse": "tnefparse",
}
"""§16.2 version key → installed distribution name; `python` is reported separately."""

VERSION_ABSENT = "absent"


@dataclass(frozen=True)
class Closure:
    """Everything the candidate's identity is a function of (§1.4/§3.11)."""

    code_files: Mapping[str, str]
    config_artifacts: Mapping[str, str | None]
    env_allowlist: tuple[str, ...]
    criteria_sha256: str
    migrations_digest: str
    corpus_digest: str
    fixtures_digest: str
    versions: Mapping[str, str]
    # CLI option values of mixed shape (str/bool/None/Path): `object` is the honest annotation.
    cli_options: Mapping[str, object]
    runner_folder: str
    editable_scope: tuple[str, ...]


def _iter_scope_files(root: Path) -> Iterator[Path]:
    """Yield every regular file under `root`, excluding ONLY bytecode (`hashing.is_bytecode`).

    §16.16(r)/A26: bytecode is recognised by its suffix chain — `.pyc`/`.pyo` and CPython's
    temporary `x.cpython-312.pyc.140213` — and `__pycache__` is not itself excluded, so a
    NON-bytecode file placed there inside the editable scope is hashed like any source file.
    """
    for path in sorted(root.rglob("*")):
        if path.is_file() and not is_bytecode(path):
            yield path


def _collect_code_files(repo_root: Path) -> dict[str, str]:
    """Map every editable-scope file and dependency pin to its sha256, repo-relative posix."""
    collected: dict[str, str] = {}
    for scope in EDITABLE_SCOPE:
        scope_root = repo_root / scope
        if not scope_root.is_dir():
            continue
        for path in _iter_scope_files(scope_root):
            collected[path.relative_to(repo_root).as_posix()] = sha256_file(path)
    for relative in DEPENDENCY_FILES:
        path = repo_root / relative
        if path.is_file():
            collected[relative] = sha256_file(path)
    return collected


def _collect_config_artifacts(repo_root: Path, criteria: CriteriaFile) -> dict[str, str | None]:
    """Map each declared config artifact to its sha256, or to None when it must not be hashed."""
    artifacts: dict[str, str | None] = {}
    for artifact in criteria.config_files:
        path = repo_root / artifact.path
        artifacts[artifact.path] = sha256_file(path) if artifact.hashed and path.is_file() else None
    return artifacts


def build_closure(
    repo_root: Path,
    criteria: CriteriaFile,
    *,
    corpus: CorpusIdentity,
    migrations_digest: str,
    fixtures_digest: str,
    cli_options: Mapping[str, object],
) -> Closure:
    """Build the run closure BEFORE any gate is evaluated (§3.2 step 5).

    `repo_root` is the parent of `backend/`; `criteria` declares the config artifacts and env
    allowlist entering `config_hash`; `corpus`, `migrations_digest` and `fixtures_digest` are the
    identities of the measured data and `cli_options` this run's option values.
    """
    return Closure(
        code_files=_collect_code_files(repo_root),
        config_artifacts=_collect_config_artifacts(repo_root, criteria),
        env_allowlist=tuple(criteria.env_allowlist),
        criteria_sha256=criteria_sha256(PACKAGED_CRITERIA_PATH),
        migrations_digest=migrations_digest,
        corpus_digest=corpus.corpus_digest,
        fixtures_digest=fixtures_digest,
        versions=versions(),
        cli_options=dict(cli_options),
        runner_folder=RUNNER_FOLDER,
        editable_scope=EDITABLE_SCOPE,
    )


def code_hash(closure: Closure) -> str:
    """Return the §3.11 `code_hash` — `canonical_lines_digest` over `<path>\\t<sha256>` lines."""
    return canonical_lines_digest(
        f"{path}\t{digest}\n" for path, digest in closure.code_files.items()
    )


def _canonical_json(payload: object) -> bytes:
    """Canonical JSON bytes: sorted keys, no whitespace, non-ASCII kept raw, Paths as strings."""
    return json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode("utf-8")


def config_hash(closure: Closure) -> str:
    """Return the §3.11 `config_hash` over the declared configuration of this run.

    It covers the criteria digest, every declared config artifact (its sha256 when `hashed:
    true`, `None` otherwise — a secret-bearing file's bytes never enter), the env allowlist NAMES,
    the migrations / corpus / fixtures digests, the versions and the CLI options.
    """
    payload = {
        "criteria_sha256": closure.criteria_sha256,
        "config_artifacts": dict(closure.config_artifacts),
        "env_allowlist": list(closure.env_allowlist),
        "migrations_digest": closure.migrations_digest,
        "corpus_digest": closure.corpus_digest,
        "fixtures_digest": closure.fixtures_digest,
        "versions": dict(closure.versions),
        "cli_options": dict(closure.cli_options),
    }
    return sha256_bytes(_canonical_json(payload))


def versions() -> dict[str, str]:
    """Return the eleven library version keys of §16.2 (`python` plus ten distributions)."""
    reported = {"python": platform.python_version()}
    for key, distribution in LIBRARY_DISTRIBUTIONS.items():
        try:
            reported[key] = distribution_version(distribution)
        except PackageNotFoundError:
            reported[key] = VERSION_ABSENT
    return reported


async def db_versions(conn: object) -> dict[str, str]:
    """Return §16.2's `postgres`/`pgvector` from the async session `conn` (no `vector`: absent)."""
    row = (
        await conn.execute(  # type: ignore[attr-defined]  # AsyncSession, typed loosely by contract
            text(
                "SELECT current_setting('server_version'), "
                "(SELECT extversion FROM pg_extension WHERE extname = 'vector')"
            )
        )
    ).one()
    return {"postgres": str(row[0]), "pgvector": str(row[1]) if row[1] else VERSION_ABSENT}


async def migrations_digest(conn: object) -> str:
    """Return the digest of the database's migration state (§3.11).

    Covers the Alembic head AND the public schema's column catalogue, so two databases agree
    when the same migrations produced it. `conn`: an open read-only session (the R6 snapshot).
    """
    # Migration 0013 revoked `alembic_version` from the write role; the revision comes through
    # the sanctioned global-plane helper of §16.15, keyed by this session's own database.
    from tools.mem01_verify.db import read_alembic_version  # local: avoids an import cycle

    database = (
        await conn.execute(text("SELECT current_database()"))  # type: ignore[attr-defined]
    ).scalar_one()
    revision = await read_alembic_version(str(database))
    columns = (
        await conn.execute(  # type: ignore[attr-defined]
            text(
                "SELECT table_name, column_name, data_type, is_nullable "
                "FROM information_schema.columns WHERE table_schema = 'public'"
            )
        )
    ).all()
    head = f"alembic_version\t{revision}\n"
    columns_lines = (f"column\t{r[0]}\t{r[1]}\t{r[2]}\t{r[3]}\n" for r in columns)
    return canonical_lines_digest([head, *columns_lines])
