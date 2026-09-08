"""
Role: The frozen digests of contract §3.10 and §1.3 — sha256 over bytes and files, the merkle
      digest over a directory tree (`runner_sha256` and, with the same exclusions, the
      editable-scope walk behind `code_hash`), `canonical_lines_digest` over an unordered set of
      lines, and `is_bytecode` — the ONE predicate that names a compiled-Python artefact.
Used by: tools.mem01_verify.run_identity (code_hash) and .input_observer (the observer
      exemption), .release_manifest (`is_manifested`), .lock (the release lock), .snapshot and
      .corpus_identity (text digests), .verify_step1 (runner_sha256 printed in every verdict
      line), and the sealed oracle modules tests/tools/mem01_verify/test_hashing.py,
      test_merkle_pycache_rule.py and test_bytecode_suffix_chain.py.
Depends on: tools.mem01_verify (package folder location only); stdlib hashlib/pathlib.
Key invariants:
  - merkle line form is EXACTLY `"<posix relative path>	<sha256 of bytes>
"`; the lines are
    sorted BYTEWISE on their UTF-8 encoding (never by str order), then concatenated and hashed.
  - §16.16(r) / A26: bytecode is recognised by its SUFFIX CHAIN, not by a directory. A path is
    bytecode when, after dropping the purely NUMERIC tail suffixes CPython appends while it
    writes a cache file atomically (`x.cpython-312.pyc.140213`, `y.pyo.99`), the last remaining
    suffix is `.pyc` or `.pyo`. A file that merely carries `.pyc` mid-chain and ends in another
    real suffix (`notes.pyc.txt`) is NOT bytecode.
  - §16.16(r): the DEFAULT exclusions are those bytecode suffixes and nothing else — no
    directory is excluded, so bytecode written by one run never changes the next run's digest
    (§16.12) while any OTHER file under a `__pycache__` directory is hashed like any file and
    DOES move `merkle_sha256`, hence `runner_sha256` (A24: a directory exclusion would be an
    undeclared channel through which content could reach the tree unhashed).
  - `is_bytecode` is that default expressed as a predicate: `merkle_sha256`'s default walk,
    `run_identity`'s scope walk, the observer exemption and `release_manifest.is_manifested`
    all decide "is this bytecode?" through THIS function and nowhere else.
  - Digests are lowercase hex and depend on file CONTENT and RELATIVE PATH only — never on
    creation order, mtimes, or the absolute location of the tree.
  - Pure and side-effect free: nothing here writes, and nothing reads outside `root`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

_READ_CHUNK_BYTES = 1024 * 1024

DEFAULT_EXCLUDE_DIRS: frozenset[str] = frozenset()
"""§16.16(r): no directory is excluded by default — only explicit callers name one."""

DEFAULT_EXCLUDE_SUFFIXES: frozenset[str] = frozenset({".pyc", ".pyo"})
"""§16.16(r): bytecode, and only bytecode, is invisible to the default digest."""


def _effective_suffix(path: Path) -> str | None:
    """Return the path's last suffix once CPython's numeric temp tail is dropped.

    While it writes a cache file, CPython creates `<final name>.<id>` and renames it, so
    `x.cpython-312.pyc.140213` and `y.pyo.99` are bytecode under a different last suffix. Every
    trailing suffix whose body is all digits is dropped; what remains is the file's real kind.

    Args:
        path: The path to inspect (its name alone is read; nothing touches the filesystem).

    Returns:
        The effective suffix including its dot (`".pyc"`), or None when nothing remains — a
        name with no suffix at all, or one whose every suffix was numeric.
    """
    suffixes = list(path.suffixes)
    while suffixes and suffixes[-1][1:].isdigit():
        suffixes.pop()
    return suffixes[-1] if suffixes else None


def is_bytecode(path: Path) -> bool:
    """True when `path` names compiled Python — the §16.16(r)/A26 suffix-chain rule.

    The one predicate behind every bytecode exclusion in the instrument: `merkle_sha256`'s
    default walk, `run_identity`'s editable-scope walk, the input observer's exemption and
    `release_manifest.is_manifested`. No directory takes part in the decision, so a
    non-bytecode file under `__pycache__` is an ordinary file everywhere.

    Args:
        path: The path to classify; only its name is read.

    Returns:
        True for `.pyc`/`.pyo`, including CPython's temporary `x.cpython-312.pyc.140213` and
        `y.pyo.99`; False for a source, for `__pycache__/oracle.json`, and for a file that only
        carries `.pyc` mid-chain and ends in another real suffix (`notes.pyc.txt`).
    """
    return _effective_suffix(path) in DEFAULT_EXCLUDE_SUFFIXES


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase hex sha256 of `data`."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Return the lowercase hex sha256 of the file's bytes, read in chunks.

    Args:
        path: The regular file to hash.

    Returns:
        The 64-character lowercase hex digest.

    Raises:
        OSError: If the file cannot be opened or read (callers wrap this where the contract
            names an instrument error).
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _merkle_lines(
    root: Path,
    exclude_dirs: frozenset[str],
    exclude_suffixes: frozenset[str],
) -> list[bytes]:
    """Return the encoded `"<relative posix>\t<sha256>\n"` line for every included file."""
    lines: list[bytes] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in exclude_dirs for part in relative.parts[:-1]):
            continue
        if _effective_suffix(path) in exclude_suffixes:
            continue
        lines.append(f"{relative.as_posix()}\t{sha256_file(path)}\n".encode())
    return lines


def merkle_sha256(
    root: Path,
    *,
    exclude_dirs: frozenset[str] = DEFAULT_EXCLUDE_DIRS,
    exclude_suffixes: frozenset[str] = DEFAULT_EXCLUDE_SUFFIXES,
) -> str:
    """Return the §3.10 merkle digest over every regular file under `root`.

    For each included file the line `"<posix relative path>\t<sha256 of bytes>\n"` is built;
    the lines are sorted bytewise and the sha256 of their concatenation is the digest.

    Args:
        root: The directory to walk recursively.
        exclude_dirs: Directory NAMES excluded at any depth (matched on the parent components
            of the relative path, so a FILE with such a name is still hashed). EMPTY by default
            (§16.16(r)); it stays a parameter for the callers that name a directory explicitly.
        exclude_suffixes: File suffixes excluded, matched on the EFFECTIVE suffix (the last
            one once CPython's numeric temp tail is dropped, §16.16(r)) — by default the
            bytecode suffixes `.pyc` and `.pyo`, at any depth, `__pycache__` included, so the
            default walk is exactly `is_bytecode`.

    Returns:
        The 64-character lowercase hex digest. An empty (or fully excluded) tree yields the
        sha256 of the empty byte string.
    """
    return sha256_bytes(b"".join(sorted(_merkle_lines(root, exclude_dirs, exclude_suffixes))))


def runner_sha256() -> str:
    """Return the §3.10 merkle digest over the instrument package's own directory.

    The digest covers `backend/tools/mem01_verify/` recursively with the default exclusions, so
    it changes whenever any instrument source, fixture module, or release annex changes — and,
    per §16.16(r), whenever a NON-bytecode file appears under one of its `__pycache__`
    directories; only what `is_bytecode` names stays invisible.
    """
    return merkle_sha256(Path(__file__).resolve().parent)


def canonical_lines_digest(lines: Iterable[str]) -> str:
    """Return the sha256 over the bytewise-sorted UTF-8 lines, each terminated by a newline.

    The input order is irrelevant; a line that does not already end with `\n` gets one, so the
    caller may pass either form. Any iterable (including a one-shot iterator) is accepted.

    Args:
        lines: The lines to digest — typically `"<path>\t<sha256>"` rows.

    Returns:
        The 64-character lowercase hex digest.
    """
    encoded = [line.encode("utf-8") for line in lines]
    terminated = [line if line.endswith(b"\n") else line + b"\n" for line in encoded]
    return sha256_bytes(b"".join(sorted(terminated)))
