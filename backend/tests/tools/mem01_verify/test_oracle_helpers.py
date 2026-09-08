"""
Role: Self-tests of the oracle's own reference helpers (reference.py). These PASS today: they
      prove the expected-value computers without the instrument (test-env brief §5 (b)).
Used by: the seal review — a green helper suite is the precondition for trusting every
      expected value the sealed tests derive from reference.py.
Depends on: tests.tools.mem01_verify.reference, stdlib. No database, no instrument.
Key invariants:
  - Every expected value here is hand-computed from the contract text, never from reference.py
    itself (a helper cannot vouch for itself).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest

from tests.tools.mem01_verify import reference

ELLIPSIS = chr(0x2026)
ZWSP = chr(0x200B)
NBSP = chr(0x00A0)
EM_DASH = chr(0x2014)
LOW_9_QUOTE = chr(0x201E)
HIGH_6_QUOTE = chr(0x201C)


def test_canonical_lines_digest_sorts_bytewise_and_terminates_lines() -> None:
    lines = ["b\t1\n", "a\t2\n", "Ж\t3"]  # Ж sorts after ASCII bytewise; last lacks \n

    digest = reference.canonical_lines_digest_reference(lines)

    expected = hashlib.sha256("a\t2\nb\t1\nЖ\t3\n".encode()).hexdigest()
    assert digest == expected


def test_merkle_reference_excludes_pycache_and_pyc_and_uses_posix_paths(tmp_path: Path) -> None:
    (tmp_path / "pkg" / "sub").mkdir(parents=True)
    (tmp_path / "pkg" / "a.py").write_bytes(b"alpha")
    (tmp_path / "pkg" / "sub" / "b.py").write_bytes(b"beta")
    (tmp_path / "pkg" / "__pycache__").mkdir()
    (tmp_path / "pkg" / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"junk")
    (tmp_path / "pkg" / "c.pyc").write_bytes(b"junk")

    digest = reference.merkle_sha256_reference(tmp_path / "pkg")

    line_a = f"a.py\t{hashlib.sha256(b'alpha').hexdigest()}\n"
    line_b = f"sub/b.py\t{hashlib.sha256(b'beta').hexdigest()}\n"
    assert digest == hashlib.sha256((line_a + line_b).encode()).hexdigest()


def test_expected_group_id_is_sha256_of_sorted_canonical_ids_joined_by_newline() -> None:
    first = UUID("00000000-0000-0000-0000-000000000002")
    second = UUID("00000000-0000-0000-0000-000000000001")

    group_id = reference.expected_group_id([first, second])

    joined = f"{second}\n{first}".encode()
    assert group_id == hashlib.sha256(joined).hexdigest()


def test_normalize_reference_expands_ellipsis_into_one_unit_of_three() -> None:
    normalized = reference.normalize_reference(f"a{ELLIPSIS}b")

    assert normalized.text == "a...b"
    assert normalized.source_positions == (0, 1, 1, 1, 2)
    assert normalized.unit_starts == frozenset({0, 1, 4})


def test_normalize_reference_collapses_runs_removes_zero_width_and_strips() -> None:
    normalized = reference.normalize_reference(f"  a \t\r\n{ZWSP} b{ZWSP}c  ")

    assert normalized.text == "a bc"
    # indices: 0-1 spaces, 2 'a', 3-6 run, 7 ZWSP, 8 ' ', 9 'b', 10 ZWSP, 11 'c', 12-13 spaces
    # the run unit spans [3, 9): the ZWSP at 7 sits inside it, the last space is 8
    assert normalized.source_positions == (2, 3, 9, 11)
    assert normalized.unit_spans[1] == (1, 3, 9)


def test_normalize_reference_maps_quotes_dashes_and_nbsp_and_keeps_cyrillic() -> None:
    normalized = reference.normalize_reference(
        f"{LOW_9_QUOTE}Здравей{HIGH_6_QUOTE}{NBSP}свят{EM_DASH}OK"
    )

    assert normalized.text == '"Здравей" свят-OK'
    assert len(normalized.source_positions) == len(normalized.text)


def test_to_original_reference_covers_whole_whitespace_run_and_rejects_inner_boundaries() -> None:
    normalized = reference.normalize_reference("a \t\r\n b")

    assert reference.to_original_reference(normalized, 0, 2) == (0, 6)
    assert reference.to_original_reference(normalized, 0, 3) == (0, 7)
    assert reference.to_original_reference(normalized, 2, 3) == (6, 7)
    assert reference.to_original_reference(normalized, 1, 2) == (1, 6)
    with pytest.raises(ValueError):
        reference.to_original_reference(reference.normalize_reference(f"x{ELLIPSIS}"), 1, 2)


def test_resolve_reference_counts_overlapping_occurrences_and_refuses_inner_dots() -> None:
    assert reference.resolve_reference("aa", "aaa") == (
        "ambiguous",
        frozenset({(0, 2), (1, 3)}),
    )
    assert reference.resolve_reference(".", f"x{ELLIPSIS}y") == ("unresolved", frozenset())
    assert reference.resolve_reference("", "abc") == ("unresolved", frozenset())
    assert reference.resolve_reference(ZWSP, "abc") == ("unresolved", frozenset())
    assert reference.resolve_reference("свят", "Здравей, свят") == (
        "resolved",
        frozenset({(9, 13)}),
    )


def test_utf8_byte_offsets_reference_counts_cyrillic_as_two_bytes() -> None:
    assert reference.utf8_byte_offsets_reference("Здравей, свят", 9, 13) == (16, 24)


def test_trim_edges_strips_only_edge_scalars_that_vanish_or_are_whitespace() -> None:
    assert reference.trim_edges(f"{ZWSP} a{NBSP}b {ZWSP}") == f"a{NBSP}b"


def test_extract_machine_block_requires_exactly_one_block() -> None:
    body = json.dumps({"k": "Здравей"}, ensure_ascii=False)
    one = f"noise\nMEM01_RESULT_V1_BEGIN\n{body}\nMEM01_RESULT_V1_END\nverdict\n"

    assert reference.extract_machine_block(one) == {"k": "Здравей"}
    with pytest.raises(ValueError):
        reference.extract_machine_block("no block here")
    with pytest.raises(ValueError):
        reference.extract_machine_block(one + one)


def test_last_nonempty_line_ignores_trailing_blank_lines_and_crlf() -> None:
    assert reference.last_nonempty_line("a\r\nb\r\n\r\n  \r\n") == "b"
    assert reference.last_nonempty_line("") == ""


@dataclass(frozen=True)
class _Record:
    case_id: str
    criterion_id: str
    origin: str
    expected: object


@dataclass(frozen=True)
class _Holder:
    by_cell: dict[str, tuple[_Record, ...]]
    extra: list[object]


def test_collect_fixture_records_walks_dataclasses_mappings_and_sequences() -> None:
    record = _Record("c1", "vis.no_forbidden_rows", "oracle", True)
    holder = _Holder(by_cell={"cell": (record,)}, extra=[[_Record("c2", "x", "o", 1)], "text"])

    records = reference.collect_fixture_records(holder)

    assert {r.case_id for r in records} == {"c1", "c2"}
    assert "text" in reference.collect_strings(holder)


def test_fixtures_digest_reference_covers_only_py_files_with_package_relative_paths(
    tmp_path: Path,
) -> None:
    package = tmp_path / "fixtures"
    (package / "sub" / "__pycache__").mkdir(parents=True)
    (package / "__init__.py").write_bytes(b"")
    (package / "a.py").write_bytes(b"A = 1\n")
    (package / "sub" / "b.py").write_bytes(b"B = 2\n")
    (package / "sub" / "__pycache__" / "b.cpython-312.pyc").write_bytes(b"\x00")
    (package / "notes.txt").write_bytes(b"not fixture source")
    expected_lines = sorted(
        f"{rel}\t{hashlib.sha256(payload).hexdigest()}\n".encode()
        for rel, payload in (
            ("__init__.py", b""),
            ("a.py", b"A = 1\n"),
            ("sub/b.py", b"B = 2\n"),
        )
    )

    digest = reference.fixtures_digest_reference(package)

    assert digest == hashlib.sha256(b"".join(expected_lines)).hexdigest()
    (package / "sub" / "b.py").write_bytes(b"B = 3\n")
    assert reference.fixtures_digest_reference(package) != digest


def test_unicode_white_space_table_is_exactly_the_property_and_excludes_zero_width() -> None:
    import unicodedata

    listed = reference.UNICODE_WHITE_SPACE

    assert all(unicodedata.category(c) in ("Zs", "Zl", "Zp", "Cc") for c in listed)
    assert {"\x85", "\u1680", "\u2028", "\u2029", "\u205f", "\u3000", "\u000b"} <= listed
    assert reference.MAPPED_SPACES <= listed
    assert "\u200b" not in listed and "\u200b" in reference.REMOVED
    assert reference.normalize_reference("a\u2028\u0085b").text == "a b"


class _StubResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one(self) -> object:
        return self._value


class _StubSession:
    def __init__(self, database: object) -> None:
        self.database = database

    async def execute(self, statement: object) -> _StubResult:
        assert "current_database()" in str(statement)
        return _StubResult(self.database)


async def test_probe_guard_accepts_probe_names_and_refuses_everything_else() -> None:
    from tests.tools.mem01_verify import seeding_rows

    assert (
        await seeding_rows.assert_probe_connection(_StubSession("mem01_probe_x")) == "mem01_probe_x"
    )
    for name in ("oneai", "postgres", "", None, "MEM01_PROBE_x", "xmem01_probe_"):
        with pytest.raises(seeding_rows.ProbeGuardError):
            await seeding_rows.assert_probe_connection(_StubSession(name))


def test_flip_one_hex_digit_always_differs() -> None:
    from tests.tools.mem01_verify import synthetic_release

    for digest in ("0" * 64, "f" * 64, "a1" * 32):
        assert synthetic_release.flip_one_hex_digit(digest) != digest


def test_clean_child_env_drops_python_utf8_crutches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONUTF8", "1")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")

    env = reference.clean_child_env({"MEM01_ORACLE": "x"})

    assert "PYTHONUTF8" not in env and "PYTHONIOENCODING" not in env
    assert env["MEM01_ORACLE"] == "x"
