"""
Role: Seals contract §16.16(r) / fix-registry row A24 for `hashing.merkle_sha256` — with the
      DEFAULT exclusions only bytecode (`.pyc`/`.pyo`) is excluded, anywhere in the tree; every
      other file under a `__pycache__` directory is hashed like any file, so a non-bytecode file
      smuggled there moves the digest while bytecode never does. An explicit `exclude_dirs`
      still drops a named directory (the parameter stays for explicit callers), and the
      signature defaults are exactly the §1.3 ones `runner_sha256()` relies on.
Used by: the seal review; the mutation check (a `merkle_sha256` that drops a whole `__pycache__`
      directory is caught here — the mutation the closed seal missed).
Depends on: tools.mem01_verify.hashing (imported inside each test); tests.tools.mem01_verify
      .reference (`sha256_hex`); stdlib.
Key invariants:
  - The expected digest is computed IN THE TEST from the §3.10 line form over the files the
    rule admits (bytewise-sorted `"<posix relative>\\t<sha256>\\n"` lines), never through the
    instrument; every tree is built by hand in tmp_path.
"""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import InstrumentLoader

SOURCE = {"a.py": b"print('a')\n", "sub/c.txt": b"c\n"}
BYTECODE = {
    "__pycache__/x.cpython-312.pyc": b"\x00bytecode of x",
    "__pycache__/y.pyo": b"\x00optimised bytecode of y",
    "sub/b.pyc": b"\x00stray bytecode",
    "sub/__pycache__/z.pyo": b"\x00nested optimised bytecode",
}
SMUGGLED = {"__pycache__/oracle.json": b'{"smuggled": "hidden split ids"}\n'}


def _tree(root: Path, *layers: dict[str, bytes]) -> Path:
    for layer in layers:
        for relative, payload in layer.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
    return root


def _expected(*layers: dict[str, bytes]) -> str:
    """The §3.10 merkle digest over exactly the given files (hand-computed reference)."""
    lines = [
        f"{relative}\t{reference.sha256_hex(payload)}\n".encode()
        for layer in layers
        for relative, payload in layer.items()
    ]
    return hashlib.sha256(b"".join(sorted(lines))).hexdigest()


def test_default_merkle_hashes_non_bytecode_under_pycache_and_excludes_bytecode_anywhere(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    hashing = instrument("hashing")
    root = _tree(tmp_path / "tree", SOURCE, BYTECODE, SMUGGLED)

    digest = hashing.merkle_sha256(root)

    assert digest == _expected(SOURCE, SMUGGLED)
    assert digest != _expected(SOURCE)  # the smuggled file is part of the digest
    assert digest != _expected(SOURCE, BYTECODE, SMUGGLED)  # bytecode never is


def test_a_smuggled_file_under_pycache_moves_the_digest_while_bytecode_does_not(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    hashing = instrument("hashing")
    root = _tree(tmp_path / "tree", SOURCE)
    base = hashing.merkle_sha256(root)

    _tree(root, BYTECODE)
    with_bytecode = hashing.merkle_sha256(root)
    _tree(root, SMUGGLED)
    with_smuggled = hashing.merkle_sha256(root)
    (root / "__pycache__" / "oracle.json").write_bytes(b'{"smuggled": "other ids"}\n')
    with_changed_smuggled = hashing.merkle_sha256(root)

    assert base == _expected(SOURCE)
    assert with_bytecode == base  # positive control: bytecode is invisible
    assert with_smuggled != base and with_changed_smuggled != with_smuggled


def test_an_explicit_exclude_dirs_still_drops_the_named_directory(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    hashing = instrument("hashing")
    root = _tree(tmp_path / "tree", SOURCE, BYTECODE, SMUGGLED)

    digest = hashing.merkle_sha256(root, exclude_dirs=frozenset({"__pycache__"}))

    assert digest == _expected(SOURCE)  # the explicit parameter keeps its meaning


def test_merkle_defaults_are_exactly_the_contract_signature(instrument: InstrumentLoader) -> None:
    hashing = instrument("hashing")

    parameters = inspect.signature(hashing.merkle_sha256).parameters

    assert parameters["exclude_dirs"].default == frozenset()
    assert parameters["exclude_suffixes"].default == frozenset({".pyc", ".pyo"})
