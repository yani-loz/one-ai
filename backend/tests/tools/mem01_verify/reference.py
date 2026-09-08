"""
Role: Independent reference implementations of the contract's FROZEN rules (STAGE-A-CONTRACT
      v1.2.2: §3.10 merkle digest, `canonical_lines_digest`, §6 EVID_NORM_V1 normalization /
      units / resolution, §7/§16.3 group ids, §3.3 machine-block extraction, §3.8 last-line
      rule, the §16.3 id forms, §16.1 event stamps, §16.5 census shapes, §16.10 data files) plus
      the subprocess and environment plumbing the CLI tests share. The oracle computes EXPECTED
      values here and never from the instrument (R12 applied to the instrument itself).
Used by: every test module under tests/tools/mem01_verify/ and its conftest.py.
Depends on: stdlib only. Never imports tools.mem01_verify or app.*.
Key invariants:
  - Pure and deterministic; proven by test_oracle_helpers.py, which passes WITHOUT the instrument.
  - The EVID_NORM tables below are transcribed from contract §6 character by character; they are
    the oracle's definition of the rule, not a copy of any implementation.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from tests.tools.mem01_verify.reference_norm import (
    ASCII_WHITESPACE as ASCII_WHITESPACE,
)
from tests.tools.mem01_verify.reference_norm import (
    DASHES as DASHES,
)
from tests.tools.mem01_verify.reference_norm import (
    DOUBLE_QUOTES as DOUBLE_QUOTES,
)
from tests.tools.mem01_verify.reference_norm import (
    ELLIPSIS as ELLIPSIS,
)
from tests.tools.mem01_verify.reference_norm import (
    MAPPED_SPACES as MAPPED_SPACES,
)
from tests.tools.mem01_verify.reference_norm import (
    REMOVED as REMOVED,
)
from tests.tools.mem01_verify.reference_norm import (
    SINGLE_QUOTES as SINGLE_QUOTES,
)
from tests.tools.mem01_verify.reference_norm import (
    UNICODE_WHITE_SPACE as UNICODE_WHITE_SPACE,
)
from tests.tools.mem01_verify.reference_norm import (
    WHITESPACE as WHITESPACE,
)
from tests.tools.mem01_verify.reference_norm import (
    NormalizedReference as NormalizedReference,
)
from tests.tools.mem01_verify.reference_norm import (
    normalize_reference as normalize_reference,
)
from tests.tools.mem01_verify.reference_norm import (
    resolve_reference as resolve_reference,
)
from tests.tools.mem01_verify.reference_norm import (
    to_original_reference as to_original_reference,
)
from tests.tools.mem01_verify.reference_norm import (
    trim_edges as trim_edges,
)
from tests.tools.mem01_verify.reference_norm import (
    utf8_byte_offsets_reference as utf8_byte_offsets_reference,
)

# ── §3.10 / §3.11 / §5.1 digests ──────────────────────────────────────────────────────────


def sha256_hex(data: bytes) -> str:
    """Lowercase hex sha256 of `data`."""
    return hashlib.sha256(data).hexdigest()


def canonical_lines_digest_reference(lines: Iterable[str]) -> str:
    """sha256 over the bytewise-sorted UTF-8 lines, each terminated by a newline (§1.3 hashing)."""
    encoded = [line.encode("utf-8") for line in lines]
    encoded = [line if line.endswith(b"\n") else line + b"\n" for line in encoded]
    return sha256_hex(b"".join(sorted(encoded)))


def merkle_sha256_reference(
    root: Path,
    exclude_dirs: frozenset[str] = frozenset({"__pycache__"}),
    exclude_suffixes: frozenset[str] = frozenset({".pyc"}),
) -> str:
    """§3.10: lines `<posix relative path>\\t<sha256>\\n`, sorted bytewise, sha256 of the join."""
    lines: list[bytes] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in exclude_dirs for part in relative.parts[:-1]):
            continue
        if path.suffix in exclude_suffixes:
            continue
        line = f"{relative.as_posix()}\t{sha256_hex(path.read_bytes())}\n"
        lines.append(line.encode("utf-8"))
    return sha256_hex(b"".join(sorted(lines)))


def expected_group_id(email_ids: Iterable[UUID]) -> str:
    """§7/§16.3: sha256 of the canonical str(uuid) ids, sorted bytewise, joined by a newline,
    no trailing newline."""
    return sha256_hex("\n".join(sorted(str(email_id) for email_id in email_ids)).encode("utf-8"))


# ── §3.3 / §3.8 stdout parsing ────────────────────────────────────────────────────────────

BLOCK_BEGIN = "MEM01_RESULT_V1_BEGIN"
BLOCK_END = "MEM01_RESULT_V1_END"


def extract_machine_block(stdout: str) -> dict:
    """Return the single JSON object between the BEGIN/END lines; raise if there is not one."""
    lines = stdout.splitlines()
    begins = [i for i, line in enumerate(lines) if line == BLOCK_BEGIN]
    ends = [i for i, line in enumerate(lines) if line == BLOCK_END]
    if len(begins) != 1 or len(ends) != 1 or ends[0] < begins[0]:
        raise ValueError(f"expected exactly one machine block, found {len(begins)}/{len(ends)}")
    return json.loads("\n".join(lines[begins[0] + 1 : ends[0]]))


def last_nonempty_line(text: str) -> str:
    """The last line of `text` that is not blank (CRLF tolerant); '' when there is none."""
    for line in reversed(text.splitlines()):
        if line.strip():
            return line
    return ""


# ── subprocess plumbing ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CliRun:
    """One child-process invocation, decoded strictly as UTF-8."""

    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str


def clean_child_env(overrides: dict[str, str]) -> dict[str, str]:
    """Copy the environment WITHOUT the UTF-8 crutches (R8 must hold by construction)."""
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONUTF8", "PYTHONIOENCODING"}}
    env.update(overrides)
    return env


def run_subprocess(argv: list[str], cwd: Path, env: dict[str, str], timeout: float) -> CliRun:
    """Run `argv` synchronously and decode both streams as strict UTF-8."""
    completed = subprocess.run(  # noqa: S603 - argv is built by the tests
        argv, cwd=str(cwd), env=env, capture_output=True, timeout=timeout, check=False
    )
    return CliRun(
        argv=tuple(argv),
        exit_code=completed.returncode,
        stdout=completed.stdout.decode("utf-8", errors="strict"),
        stderr=completed.stderr.decode("utf-8", errors="strict"),
    )


# ── generic walkers for fixture objects of unknown shape ──────────────────────────────────

FIXTURE_RECORD_FIELDS = ("case_id", "criterion_id", "origin", "expected")


def _iter_children(obj: object) -> Iterator[object]:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        for field in dataclasses.fields(obj):
            yield getattr(obj, field.name)
    elif isinstance(obj, dict):
        yield from obj.keys()
        yield from obj.values()
    elif isinstance(obj, list | tuple | set | frozenset):
        yield from obj


def collect_fixture_records(obj: object) -> list[object]:
    """Every dataclass instance reachable from `obj` that carries the four fixture fields."""
    found: list[object] = []
    seen: set[int] = set()
    stack: list[object] = [obj]
    while stack:
        current = stack.pop()
        if id(current) in seen or isinstance(
            current, str | bytes | int | float | bool | type(None)
        ):
            continue
        seen.add(id(current))
        if dataclasses.is_dataclass(current) and not isinstance(current, type):
            if all(hasattr(current, name) for name in FIXTURE_RECORD_FIELDS):
                found.append(current)
        stack.extend(_iter_children(current))
    return found


def collect_strings(obj: object) -> list[str]:
    """Every str reachable from `obj` through dataclasses, mappings and sequences."""
    found: list[str] = []
    seen: set[int] = set()
    stack: list[object] = [obj]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            found.append(current)
            continue
        if id(current) in seen or isinstance(current, bytes | int | float | bool | type(None)):
            continue
        seen.add(id(current))
        stack.extend(_iter_children(current))
    return found


def collect_ints(obj: object) -> list[int]:
    """Every int (not bool) reachable from `obj` through dataclasses, mappings and sequences."""
    found: list[int] = []
    seen: set[int] = set()
    stack: list[object] = [obj]
    while stack:
        current = stack.pop()
        if isinstance(current, bool):
            continue
        if isinstance(current, int):
            found.append(current)
            continue
        if id(current) in seen or isinstance(current, str | bytes | float | type(None)):
            continue
        seen.add(id(current))
        stack.extend(_iter_children(current))
    return found


# ── small sync file helpers (keep open()/Path calls out of async test bodies) ─────────────


def rglob_files(root: Path, pattern: str) -> list[Path]:
    """Sorted regular files under `root` matching `pattern` (recursive)."""
    return sorted(path for path in root.rglob(pattern) if path.is_file())


def is_file(path: Path) -> bool:
    """Path.is_file, callable from async tests."""
    return path.is_file()


def is_dir(path: Path) -> bool:
    """Path.is_dir, callable from async tests."""
    return path.is_dir()


def is_empty_dir(path: Path) -> bool:
    """True when `path` is a directory with no entries."""
    return path.is_dir() and not any(path.iterdir())


def file_size(path: Path) -> int:
    """Size in bytes."""
    return path.stat().st_size


def read_bytes(path: Path) -> bytes:
    """Path.read_bytes, callable from async tests."""
    return path.read_bytes()


def read_text(path: Path) -> str:
    """Read a UTF-8 file."""
    return path.read_text(encoding="utf-8")


def read_json(path: Path) -> dict:
    """Read a UTF-8 JSON object file."""
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    """Read a UTF-8 JSON-lines file (blank lines ignored)."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_text(path: Path, text: str) -> None:
    """Write a UTF-8 file, creating parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: object) -> None:
    """Write a UTF-8 JSON file (sorted keys, non-ASCII kept raw), creating parents."""
    write_text(path, json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1))


# ── §16 determinations: id forms, event stamps, census shapes, data files ─────────────────

RUN_ID_PATTERN = r"[0-9]{8}t[0-9]{6}z_[0-9a-f]{8}"
UUID4_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
EVENT_AT_PATTERN = r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z"
EVENT_AT = "2026-09-06T12:00:00Z"
HEX64_PATTERN = r"[0-9a-f]{64}"


def oracle_run_id(index: int, stamp: str = "20260906t120000z") -> str:
    """A run id in the §16.3 form `<YYYYMMDD>t<HHMMSS>z_<8 lowercase hex>` for test fixtures."""
    return f"{stamp}_{index & 0xFFFFFFFF:08x}"


def repository_head_revision(migrations_dir: Path) -> str:
    """The Alembic revision no migration file names as its down_revision (single-head chain)."""
    revisions: dict[str, str | None] = {}
    for path in migrations_dir.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        revision = re.search(r'^revision\s*=\s*"([^"]+)"', source, re.MULTILINE)
        down = re.search(r'^down_revision\s*=\s*("([^"]+)"|None)', source, re.MULTILINE)
        if revision:
            revisions[revision.group(1)] = down.group(2) if down and down.group(2) else None
    downs = {down for down in revisions.values() if down}
    heads = [rev for rev in revisions if rev not in downs]
    if len(heads) != 1:
        raise ValueError(f"expected one migration head, found {heads}")
    return heads[0]


def is_count_distribution(value: object) -> bool:
    """True iff `value` is a list of `{"key", "count"}` objects (§16.5 distribution shape)."""
    return isinstance(value, list) and all(
        isinstance(item, dict) and set(item) == {"key", "count"} and isinstance(item["count"], int)
        for item in value
    )


def is_ordered_distribution(value: list[dict]) -> bool:
    """§16.5 ordering: count descending, then key ascending (keys compared as strings)."""
    order = [(-item["count"], str(item["key"])) for item in value]
    return order == sorted(order)


def nonzero_entries(value: list[dict]) -> list[tuple[str, int]]:
    """The (key, count) pairs with a positive count, in the emitted order."""
    return [(str(item["key"]), item["count"]) for item in value if item["count"] > 0]


def gold_id_lines(gold_ids: Iterable[str]) -> bytes:
    """A §16.10 data file: one `{"gold_id": ...}` object per line, UTF-8."""
    return "".join(json.dumps({"gold_id": gold_id}) + "\n" for gold_id in gold_ids).encode("utf-8")


def as_path(value: object) -> Path:
    """A Path from a string the instrument recorded (e.g. `protected_result_path`)."""
    return Path(str(value))


def parse_jsonl_bytes(payload: bytes) -> list[dict]:
    """Parse complete JSONL bytes (one object per line; blank lines ignored)."""
    return [json.loads(line) for line in payload.decode("utf-8").splitlines() if line.strip()]


def fixtures_digest_reference(package_dir: Path) -> str:
    """§16.9: merkle over every `.py` file of the fixtures package, package-relative posix paths."""
    lines: list[bytes] = []
    for path in sorted(package_dir.rglob("*.py")):
        relative = path.relative_to(package_dir)
        if not path.is_file() or "__pycache__" in relative.parts:
            continue
        lines.append(f"{relative.as_posix()}\t{sha256_hex(path.read_bytes())}\n".encode())
    return sha256_hex(b"".join(sorted(lines)))
