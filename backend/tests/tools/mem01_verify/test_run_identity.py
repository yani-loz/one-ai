"""
Role: Seals the closure of contract §3.11 / §1.3 `run_identity` on a synthetic repository —
      `code_hash` covers exactly the editable scope plus the dependency pins (never the runner
      folder or docs), `config_hash` covers the declared artifacts, allowlist names, digests,
      versions and CLI options (never the unhashed secret file), `versions`, `new_run_id`,
      `migrations_digest` on the probe, and the input observer's offender detection.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.run_identity, .criteria, .corpus_identity, .db (imported inside
      each test); tests.tools.mem01_verify.reference (canonical lines digest).
Key invariants:
  - The synthetic repo is built from scratch in tmp_path; the real repo is never hashed here.
  - `InputObserver` is constructed with the repo root as its only positional argument (the
    contract gives no constructor signature; flagged in the report). `versions()` is sealed
    to exactly the eleven section-16.2 library keys and `new_run_id` to the 16.3 form.
"""

from __future__ import annotations

import os
import platform
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import (
    SESSION_LOOP,
    InstrumentLoader,
    ProbeCorpusFactory,
)

HEX = "0" * 64
LIBRARY_VERSION_KEYS = (
    "python",
    "sqlalchemy",
    "asyncpg",
    "charset_normalizer",
    "html2text",
    "striprtf",
    "pdfplumber",
    "pypdf",
    "python_docx",
    "openpyxl",
    "tnefparse",
)
CODE_FILES = {
    "backend/app/a.py": b"print('a')\n",
    "backend/app/sub/b.py": b"print('b')\n",
    "backend/tests/mem01/test_x.py": b"def test_x(): pass\n",
    "backend/pyproject.toml": b"[project]\nname='x'\n",
    "backend/uv.lock": b"version = 1\n",
    "backend/.python-version": b"3.12\n",
}
OTHER_FILES = {
    "backend/tools/mem01_verify/z.py": b"# runner folder\n",
    "docs/x.md": b"# docs\n",
    "backend/alembic.ini": b"[alembic]\n",
    "backend/.env": b"POSTGRES_HOST=localhost\n",
}


def _synthetic_repo(root: Path) -> Path:
    for relative, payload in {**CODE_FILES, **OTHER_FILES}.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return root


def _corpus_identity(instrument: InstrumentLoader, corpus_digest: str = HEX) -> object:
    corpus_identity = instrument("corpus_identity")
    return corpus_identity.CorpusIdentity(
        version="CORPUS_DIGEST_V1",
        corpus_digest=corpus_digest,
        text_digest="1" * 64,
        roster_counts={"email_message": 6},
        taken_at=datetime.now(UTC),
        snapshot_transaction_id="00000A1B-1",
        database="mem01_probe_oracle",
        host="localhost",
        port=5432,
        org_id=uuid4(),
    )


def _closure(
    instrument: InstrumentLoader,
    repo: Path,
    criteria_path: Path,
    *,
    corpus_digest: str = HEX,
    migrations: str = HEX,
    fixtures: str = HEX,
    cli_options: dict | None = None,
) -> object:
    run_identity = instrument("run_identity")
    criteria = instrument("criteria").load_criteria(criteria_path)
    return run_identity.build_closure(
        repo,
        criteria,
        corpus=_corpus_identity(instrument, corpus_digest),
        migrations_digest=migrations,
        fixtures_digest=fixtures,
        cli_options=cli_options if cli_options is not None else {"gates": None},
    )


def test_code_hash_covers_exactly_the_editable_scope_and_dependency_pins(
    instrument: InstrumentLoader, criteria_path: Path, tmp_path: Path
) -> None:
    run_identity = instrument("run_identity")
    repo = _synthetic_repo(tmp_path / "repo")

    closure = _closure(instrument, repo, criteria_path)

    assert set(closure.code_files) == set(CODE_FILES)
    assert all(closure.code_files[p] == reference.sha256_hex(b) for p, b in CODE_FILES.items())
    assert run_identity.code_hash(closure) == reference.canonical_lines_digest_reference(
        f"{path}\t{digest}\n" for path, digest in closure.code_files.items()
    )
    assert tuple(closure.editable_scope) and closure.runner_folder


def test_code_hash_moves_with_editable_files_and_ignores_docs_and_the_runner_folder(
    instrument: InstrumentLoader, criteria_path: Path, tmp_path: Path
) -> None:
    run_identity = instrument("run_identity")
    repo = _synthetic_repo(tmp_path / "repo")
    baseline = run_identity.code_hash(_closure(instrument, repo, criteria_path))

    (repo / "docs" / "x.md").write_bytes(b"# changed docs\n")
    (repo / "backend" / "tools" / "mem01_verify" / "z.py").write_bytes(b"# changed runner\n")
    (repo / "backend" / "app" / "__pycache__").mkdir()
    (repo / "backend" / "app" / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"\x00pyc")
    (repo / "backend" / "app" / "sub" / "b.pyc").write_bytes(b"\x00stray bytecode")
    bytecode = _closure(instrument, repo, criteria_path)
    unchanged = run_identity.code_hash(bytecode)
    (repo / "backend" / "app" / "a.py").write_bytes(b"print('A')\n")
    app_changed = run_identity.code_hash(_closure(instrument, repo, criteria_path))
    (repo / "backend" / "uv.lock").write_bytes(b"version = 2\n")
    lock_changed = run_identity.code_hash(_closure(instrument, repo, criteria_path))

    assert unchanged == baseline  # docs, the runner folder and bytecode never move it
    assert not any(p.endswith(".pyc") or "__pycache__" in p for p in bytecode.code_files)
    assert app_changed != baseline and lock_changed != app_changed


def test_config_hash_covers_declared_inputs_but_never_the_secret_file(
    instrument: InstrumentLoader, criteria_path: Path, tmp_path: Path
) -> None:
    run_identity = instrument("run_identity")
    repo = _synthetic_repo(tmp_path / "repo")
    base = _closure(instrument, repo, criteria_path)
    baseline = run_identity.config_hash(base)

    assert base.config_artifacts["backend/.env"] is None
    assert base.config_artifacts["backend/alembic.ini"] == reference.sha256_hex(b"[alembic]\n")
    (repo / "backend" / ".env").write_bytes(b"POSTGRES_HOST=elsewhere\n")
    assert run_identity.config_hash(_closure(instrument, repo, criteria_path)) == baseline
    (repo / "backend" / "alembic.ini").write_bytes(b"[alembic]\nx=1\n")
    assert run_identity.config_hash(_closure(instrument, repo, criteria_path)) != baseline
    variants = [
        _closure(instrument, repo, criteria_path, cli_options={"gates": "SNAP"}),
        _closure(instrument, repo, criteria_path, corpus_digest="2" * 64),
        _closure(instrument, repo, criteria_path, migrations="3" * 64),
        _closure(instrument, repo, criteria_path, fixtures="4" * 64),
    ]
    hashes = {run_identity.config_hash(variant) for variant in variants}
    assert len(hashes) == 4 and baseline not in hashes
    assert tuple(base.env_allowlist) == tuple(
        instrument("criteria").load_criteria(criteria_path).env_allowlist
    )
    assert base.criteria_sha256 == reference.sha256_hex(criteria_path.read_bytes())


def test_config_hash_moves_with_the_env_allowlist_names(
    instrument: InstrumentLoader, criteria_yaml: dict, criteria_path: Path, tmp_path: Path
) -> None:
    import yaml

    run_identity = instrument("run_identity")
    repo = _synthetic_repo(tmp_path / "repo")
    widened = dict(criteria_yaml)
    widened["env_allowlist"] = [*criteria_yaml["env_allowlist"], "MEM01_ORACLE_EXTRA"]
    widened_path = tmp_path / "criteria.widened.yaml"
    widened_path.write_text(
        yaml.safe_dump(widened, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    original = run_identity.config_hash(_closure(instrument, repo, criteria_path))
    changed = run_identity.config_hash(_closure(instrument, repo, widened_path))

    assert original != changed


def test_versions_reports_exactly_the_eleven_library_keys(instrument: InstrumentLoader) -> None:
    versions = instrument("run_identity").versions()

    assert set(versions) == set(LIBRARY_VERSION_KEYS)
    assert all(isinstance(value, str) and value for value in versions.values())
    assert versions["python"] == platform.python_version()


def test_new_run_id_has_the_determined_form_stamped_from_the_given_instant(
    instrument: InstrumentLoader,
) -> None:
    run_identity = instrument("run_identity")

    first = run_identity.new_run_id(datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC))
    second = run_identity.new_run_id(datetime(2026, 9, 6, 12, 0, 1, tzinfo=UTC))
    again = run_identity.new_run_id(datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC))

    assert re.fullmatch(reference.RUN_ID_PATTERN, first), first
    assert first.startswith("20260906t120000z_") and second.startswith("20260906t120001z_")
    assert first != again  # the hex suffix is fresh even for an identical instant
    assert len(f"mem01_probe_{first}") < 63


def test_input_observer_reports_repo_files_and_env_names_outside_the_closure(
    instrument: InstrumentLoader,
    criteria_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_identity = instrument("run_identity")
    repo = _synthetic_repo(tmp_path / "repo")
    closure = _closure(instrument, repo, criteria_path)
    monkeypatch.setenv("MEM01_ORACLE_FOREIGN_VAR", "1")
    monkeypatch.setenv("POSTGRES_HOST", os.environ.get("POSTGRES_HOST", "localhost"))

    with run_identity.InputObserver(repo) as observer:
        (repo / "docs" / "x.md").read_bytes()
        (repo / "backend" / "app" / "a.py").read_bytes()
        (repo / "backend" / "tools" / "mem01_verify" / "z.py").read_bytes()
        os.environ.get("MEM01_ORACLE_FOREIGN_VAR")
        os.environ.get("POSTGRES_HOST")
    offenders = [str(item).replace("\\", "/") for item in observer.check_within(closure)]

    assert any(item.endswith("docs/x.md") for item in offenders)
    assert any("MEM01_ORACLE_FOREIGN_VAR" in item for item in offenders)
    assert not any(item.endswith("backend/app/a.py") for item in offenders)
    assert not any(item.endswith("z.py") for item in offenders)
    assert not any("POSTGRES_HOST" in item for item in offenders)
    assert any(str(p).replace("\\", "/").endswith("docs/x.md") for p in observer.observed_paths)
    assert "MEM01_ORACLE_FOREIGN_VAR" in set(observer.observed_env)
    assert isinstance(observer.observed_paths, frozenset)
    assert isinstance(observer.observed_env, frozenset)
    assert all(isinstance(p, Path) for p in observer.observed_paths)


@SESSION_LOOP
async def test_migrations_digest_is_stable_and_hex_on_the_probe(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory
) -> None:
    corpus = await probe_corpus()
    db = instrument("db")
    run_identity = instrument("run_identity")

    async with db.readonly_corpus_snapshot(corpus.small.org_id, database=corpus.database) as conn:
        first = await run_identity.migrations_digest(conn)
        second = await run_identity.migrations_digest(conn)
        db_versions = await run_identity.db_versions(conn)  # async (§16.13)

    assert re.fullmatch(r"[0-9a-f]{64}", first) and first == second
    assert set(db_versions) == {"postgres", "pgvector"}
    assert all(isinstance(value, str) and value for value in db_versions.values())
