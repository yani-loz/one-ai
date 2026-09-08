"""
Role: Seals the digests of contract §3.10 (`merkle_sha256`, `runner_sha256`) and §1.3
      (`canonical_lines_digest`, `sha256_bytes`, `sha256_file`) against the oracle's own
      reference computations — order independence, `__pycache__`/`.pyc` exclusion, posix paths,
      one-byte sensitivity.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.hashing (imported inside each test),
      tests.tools.mem01_verify.reference (proven by test_oracle_helpers.py).
Key invariants:
  - Expected digests come from reference.py or from inline hashlib arithmetic, never from the
    instrument.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import InstrumentLoader


def _tree(root: Path, files: dict[str, bytes]) -> Path:
    for relative, payload in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return root


def test_sha256_bytes_and_file_match_hashlib(instrument: InstrumentLoader, tmp_path: Path) -> None:
    hashing = instrument("hashing")
    payload = "Здравей".encode()
    path = tmp_path / "payload.bin"
    path.write_bytes(payload)

    assert hashing.sha256_bytes(payload) == hashlib.sha256(payload).hexdigest()
    assert hashing.sha256_file(path) == hashlib.sha256(payload).hexdigest()


def test_merkle_matches_reference_with_posix_paths_and_exclusions(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    hashing = instrument("hashing")
    root = _tree(
        tmp_path / "pkg",
        {
            "a.py": b"alpha",
            "sub/deeper/b.py": b"beta",
            "кирилица.txt": b"c",
            "__pycache__/a.cpython-312.pyc": b"junk",
            "sub/__pycache__/x.pyc": b"junk",
            "c.pyc": b"junk",
        },
    )
    line_a = f"a.py\t{hashlib.sha256(b'alpha').hexdigest()}\n"
    line_b = f"sub/deeper/b.py\t{hashlib.sha256(b'beta').hexdigest()}\n"
    line_c = f"кирилица.txt\t{hashlib.sha256(b'c').hexdigest()}\n"
    by_hand = hashlib.sha256((line_a + line_b + line_c).encode("utf-8")).hexdigest()

    digest = hashing.merkle_sha256(root)

    assert digest == by_hand
    assert digest == reference.merkle_sha256_reference(root)


def test_merkle_ignores_creation_order_but_not_one_byte(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    hashing = instrument("hashing")
    first = _tree(tmp_path / "one", {"z.py": b"zzz", "a.py": b"aaa", "m/n.py": b"nnn"})
    second = _tree(tmp_path / "two", {"m/n.py": b"nnn", "a.py": b"aaa", "z.py": b"zzz"})
    third = _tree(tmp_path / "three", {"m/n.py": b"nnn", "a.py": b"aab", "z.py": b"zzz"})

    assert hashing.merkle_sha256(first) == hashing.merkle_sha256(second)
    assert hashing.merkle_sha256(first) != hashing.merkle_sha256(third)


def test_merkle_pycache_content_never_changes_the_digest(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    hashing = instrument("hashing")
    root = _tree(tmp_path / "pkg", {"a.py": b"alpha"})
    before = hashing.merkle_sha256(root)
    _tree(root, {"__pycache__/a.cpython-312.pyc": b"bytecode", "a.pyc": b"more"})

    assert hashing.merkle_sha256(root) == before
    # positive control: a real file changes it
    _tree(root, {"b.py": b"beta"})
    assert hashing.merkle_sha256(root) != before


def test_merkle_honours_custom_exclusions(instrument: InstrumentLoader, tmp_path: Path) -> None:
    hashing = instrument("hashing")
    root = _tree(tmp_path / "pkg", {"a.py": b"alpha", "skip/b.py": b"beta", "c.log": b"log"})
    only_a = _tree(tmp_path / "only", {"a.py": b"alpha"})

    digest = hashing.merkle_sha256(
        root, exclude_dirs=frozenset({"skip"}), exclude_suffixes=frozenset({".log"})
    )

    assert digest == hashing.merkle_sha256(only_a)
    assert digest != hashing.merkle_sha256(root)


def test_runner_sha256_is_the_merkle_of_the_package_folder(
    instrument: InstrumentLoader, runner_folder: Path
) -> None:
    hashing = instrument("hashing")

    digest = hashing.runner_sha256()

    assert len(digest) == 64 and digest == digest.lower()
    assert digest == reference.merkle_sha256_reference(runner_folder)


def test_canonical_lines_digest_sorts_bytewise_and_is_order_independent(
    instrument: InstrumentLoader,
) -> None:
    hashing = instrument("hashing")
    lines = ["b\tx\n", "a\ty\n", "Ж\tz\n", "B\tw\n"]

    digest = hashing.canonical_lines_digest(lines)

    assert digest == hashlib.sha256("B\tw\na\ty\nb\tx\nЖ\tz\n".encode()).hexdigest()
    assert digest == hashing.canonical_lines_digest(reversed(lines))
    assert digest != hashing.canonical_lines_digest(["b\tx\n", "a\tY\n", "Ж\tz\n", "B\tw\n"])
    assert digest == reference.canonical_lines_digest_reference(lines)
