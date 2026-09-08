"""
Role: Seals fix-registry row A26 / contract §16.16(r), §3.10, §16.6 — bytecode is recognised by
      its SUFFIX CHAIN, not the last suffix alone: CPython's temporary names
      (`x.cpython-312.pyc.<id>`, `y.pyo.<id>`) are bytecode exactly like `.pyc`/`.pyo`, so
      `hashing.merkle_sha256` skips them, `hashing.is_bytecode` names them, the closure's
      `code_files` never carries them, and `release_manifest.is_manifested` never manifests
      them — while a non-bytecode file under `__pycache__` is hashed, listed and manifested.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.hashing, .run_identity, .criteria, .corpus_identity,
      .release_manifest (imported inside each test); tests.tools.mem01_verify.reference
      (`sha256_hex`); conftest (criteria_path).
Key invariants:
  - The expected merkle digest is computed IN THE TEST from the §3.10 line form over the admitted
    files; every tree lives in tmp_path; the real repository is never hashed or observed.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import InstrumentLoader

HEX = "0" * 64
ADMITTED = {"a.py": b"print('a')\n", "__pycache__/oracle.json": b'{"smuggled": "ids"}\n'}
BYTECODE = {
    "__pycache__/x.cpython-312.pyc": b"\x00bytecode of x",
    "__pycache__/x.cpython-312.pyc.140213": b"\x00temporary bytecode of x",
    "sub/y.pyo.99": b"\x00temporary optimised bytecode of y",
}
TEMP_NAME = "backend/app/__pycache__/x.cpython-312.pyc.140213"
IN_SCOPE_JSON = "backend/app/__pycache__/oracle.json"
REPO_FILES = {
    "backend/app/a.py": b"print('a')\n",
    TEMP_NAME: b"\x00temporary bytecode of x",
    IN_SCOPE_JSON: b'{"kept": "a non-bytecode file inside the editable scope"}\n',
    "backend/pyproject.toml": b"[project]\nname='x'\n",
    "backend/uv.lock": b"version = 1\n",
    "backend/.python-version": b"3.12\n",
    "backend/alembic.ini": b"[alembic]\n",
    "backend/.env": b"POSTGRES_HOST=localhost\n",
}


def _tree(root: Path, *layers: dict[str, bytes]) -> Path:
    for layer in layers:
        for relative, payload in layer.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
    return root


def _expected(files: dict[str, bytes]) -> str:
    """The §3.10 merkle digest over exactly `files` (hand-computed reference)."""
    lines = [f"{rel}\t{reference.sha256_hex(data)}\n".encode() for rel, data in files.items()]
    return hashlib.sha256(b"".join(sorted(lines))).hexdigest()


def _closure(instrument: InstrumentLoader, repo: Path, criteria_path: Path) -> object:
    corpus = instrument("corpus_identity").CorpusIdentity(
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


def test_merkle_skips_bytecode_by_suffix_chain_and_hashes_the_non_bytecode_file(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    hashing = instrument("hashing")
    root = _tree(tmp_path / "tree", ADMITTED, BYTECODE)

    digest = hashing.merkle_sha256(root)

    assert digest == _expected(ADMITTED)
    assert digest != _expected({**ADMITTED, **BYTECODE})


@pytest.mark.parametrize("name", sorted(BYTECODE))
def test_is_bytecode_recognises_pyc_pyo_and_their_temporary_names(
    instrument: InstrumentLoader, name: str
) -> None:
    assert instrument("hashing").is_bytecode(Path(name)) is True


@pytest.mark.parametrize("name", ["a.py", "__pycache__/oracle.json", "notes.pyc.txt"])
def test_is_bytecode_is_false_for_sources_and_other_files(
    instrument: InstrumentLoader, name: str
) -> None:
    assert instrument("hashing").is_bytecode(Path(name)) is False


def test_closure_excludes_temporary_bytecode_names_and_keeps_non_bytecode_under_pycache(
    instrument: InstrumentLoader, criteria_path: Path, tmp_path: Path
) -> None:
    repo = _tree(tmp_path / "repo", REPO_FILES)

    closure = _closure(instrument, repo, criteria_path)

    assert TEMP_NAME not in closure.code_files  # type: ignore[attr-defined]
    assert IN_SCOPE_JSON in closure.code_files  # type: ignore[attr-defined]
    assert "backend/app/a.py" in closure.code_files  # type: ignore[attr-defined]


def test_is_manifested_follows_the_bytecode_rule_under_pycache(
    instrument: InstrumentLoader,
) -> None:
    release_manifest = instrument("release_manifest")

    assert release_manifest.is_manifested("__pycache__/x.cpython-312.pyc.140213") is False
    assert release_manifest.is_manifested("__pycache__/x.cpython-312.pyc") is False
    assert release_manifest.is_manifested("__pycache__/oracle.json") is True
    assert release_manifest.is_manifested("data/optimization/QS/part0.jsonl") is True  # control
    assert release_manifest.is_manifested("reports/oracle_run/note.txt") is False  # §16.6
