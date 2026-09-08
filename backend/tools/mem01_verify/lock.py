"""
Role: The staged release lock of contract §4.3 — `compute_lock` (the sha256 of the manifest
      bytes), stage 1 `verify_release_visible` (manifest, criteria, protocol, schemas, every
      VISIBLE file the manifest names, and the frozen runner hash — never a hidden path), and
      stage 2 `verify_hidden_split` (exactly the named split's files, resolved under the hidden
      root with the `hidden/` prefix stripped).
Used by: tools.mem01_verify.roster (it takes the verified `ReleaseInfo`), .release,
      .verify_step1 (run sequence steps 3 and 7), gates.context, and the sealed oracle module
      tests/tools/mem01_verify/test_lock.py.
Depends on: tools.mem01_verify.exceptions (`ReleaseLockError`, `RunnerHashMismatchError`) and
      tools.mem01_verify.hashing (`sha256_bytes`, `sha256_file`, `runner_sha256`, `is_bytecode`);
      stdlib.
Key invariants:
  - Stage 1 NEVER opens, stats or resolves a path whose manifest entry is `visibility: "hidden"`
    (R1): a bogus hidden hash is invisible to it.
  - Stage 2 opens ONLY the files of the split it was asked for, and only for the named sets, so
    the other split stays unread even when its manifest entry is wrong.
  - `ReleaseInfo.lock_sha256` is bare lowercase hex; `expect_lock` is accepted as `sha256:<hex>`
    or bare hex in either case and normalized before comparison (§16.2/§16.12).
  - A `frozen` release additionally requires: the recomputed `runner_sha256`, and that no file
    under the release directory other than the manifest itself, `audit.jsonl`, `reports/**` and
    bytecode is missing from `files` (§16.6). Those four exemptions are EXACTLY what
    `release_manifest.is_manifested` declines to manifest — bytecode through the one §16.16(r)
    predicate `hashing.is_bytecode`, so no directory takes part and a non-bytecode file under a
    `__pycache__` directory is an extra like any other. A `draft` tolerates extra files.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tools.mem01_verify.exceptions import ReleaseLockError, RunnerHashMismatchError
from tools.mem01_verify.hashing import is_bytecode, runner_sha256, sha256_bytes, sha256_file

MANIFEST_FILENAME = "dataset.manifest.json"
CRITERIA_FILENAME = "criteria.step1.v1.yaml"
PROTOCOL_FILENAME = "PROTOCOL.v1.md"
AUDIT_FILENAME = "audit.jsonl"
REPORTS_DIRNAME = "reports"
HIDDEN_PREFIX = "hidden/"
RELEASES_DIRNAME = "releases"

VISIBLE = "visible"
HIDDEN = "hidden"

_LOCK_PREFIX = "sha256:"
_HEX64 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class ReleaseInfo:
    """A release whose VISIBLE inputs verified against its manifest (§1.4).

    `hidden_files_verified` stays 0 until `verify_hidden_split` ran for this object; the counter
    is the only field stage 2 updates (see `_record_hidden_verified`).
    """

    path: Path
    name: str
    state: Literal["draft", "frozen"]
    lock_sha256: str
    # The parsed manifest: JSON of mixed shape (str/int/bool/list/dict), so `object` is honest.
    manifest: Mapping[str, object]
    criteria_path: Path
    visible_files_verified: int
    hidden_files_verified: int


def normalize_lock(value: str) -> str:
    """Return the bare lowercase hex form of an expected lock (§16.2).

    Args:
        value: `sha256:<hex>` or bare hex, in either case.

    Returns:
        The 64-character lowercase hex digest.

    Raises:
        ReleaseLockError: the value is not a 64-character hex digest with an optional
            case-insensitive `sha256:` prefix.
    """
    candidate = value.strip()
    if candidate.lower().startswith(_LOCK_PREFIX):
        candidate = candidate[len(_LOCK_PREFIX) :]
    candidate = candidate.lower()
    if not _HEX64.fullmatch(candidate):
        raise ReleaseLockError(
            "expected lock is not a sha256 hex digest (optionally 'sha256:'-prefixed)"
        )
    return candidate


def compute_lock(manifest_path: Path) -> str:
    """Return `release_lock_sha256` — the sha256 of the manifest file's bytes (§4.3).

    Args:
        manifest_path: path to `dataset.manifest.json`.

    Returns:
        The 64-character lowercase hex digest.

    Raises:
        ReleaseLockError: the manifest file cannot be read.
    """
    try:
        return sha256_bytes(manifest_path.read_bytes())
    except OSError as error:
        raise ReleaseLockError(
            f"release manifest unreadable at {manifest_path}: {error}"
        ) from error


def _load_manifest(manifest_path: Path) -> dict[str, object]:
    """Parse the manifest as a JSON object; ReleaseLockError on anything else."""
    try:
        parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ReleaseLockError(
            f"release manifest unreadable at {manifest_path}: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise ReleaseLockError(f"release manifest is not valid JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise ReleaseLockError("release manifest is not a JSON object")
    return parsed


def _require(manifest: Mapping[str, object], key: str, kind: type) -> object:
    """Return `manifest[key]`, refusing an absent key or a value of the wrong JSON type."""
    value = manifest.get(key)
    if not isinstance(value, kind):
        raise ReleaseLockError(f"release manifest field {key!r} is missing or has the wrong type")
    return value


def _verify_file(path: Path, expected_sha256: object, expected_bytes: object, label: str) -> None:
    """Verify one named file's sha256 (and byte length when the manifest declares one)."""
    if not isinstance(expected_sha256, str) or not _HEX64.fullmatch(expected_sha256.lower()):
        raise ReleaseLockError(f"{label}: manifest carries no usable sha256")
    if not path.is_file():
        raise ReleaseLockError(f"{label}: the manifest names a file that is not present")
    actual = sha256_file(path)
    if actual != expected_sha256.lower():
        raise ReleaseLockError(f"{label}: sha256 differs from the manifest")
    if isinstance(expected_bytes, int) and path.stat().st_size != expected_bytes:
        raise ReleaseLockError(f"{label}: byte length differs from the manifest")


def _files_entries(manifest: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    """Return the manifest's `files` map, validating each entry's shape and visibility."""
    entries = _require(manifest, "files", dict)
    verified: dict[str, Mapping[str, object]] = {}
    for relative, entry in entries.items():  # type: ignore[union-attr]
        if not isinstance(entry, dict):
            raise ReleaseLockError(f"files[{relative!r}] is not a JSON object")
        if entry.get("visibility") not in (VISIBLE, HIDDEN):
            raise ReleaseLockError(f"files[{relative!r}] has an unknown visibility")
        verified[str(relative)] = entry
    return verified


def _verify_visible_files(release_dir: Path, files: Mapping[str, Mapping[str, object]]) -> int:
    """Verify every `visibility: "visible"` entry; hidden entries are not touched (R1)."""
    verified = 0
    for relative, entry in sorted(files.items()):
        if entry["visibility"] != VISIBLE:
            continue
        _verify_file(
            release_dir / relative, entry.get("sha256"), entry.get("bytes"), f"file {relative}"
        )
        verified += 1
    return verified


def _verify_schemas(release_dir: Path, manifest: Mapping[str, object]) -> None:
    """Verify every entry of the manifest's `schemas` map (path → sha256)."""
    schemas = _require(manifest, "schemas", dict)
    for relative, digest in sorted(schemas.items()):  # type: ignore[union-attr]
        _verify_file(release_dir / str(relative), digest, None, f"schema {relative}")


def _unmanifested_files(release_dir: Path, files: Mapping[str, Mapping[str, object]]) -> list[str]:
    """Return the release-relative paths present on disk that `files` does not name (§16.6).

    `dataset.manifest.json`, the release `audit.jsonl`, everything under `reports/` and bytecode
    are never manifested and are therefore never reported here. Those are the same four
    exemptions `release_manifest.is_manifested` applies when the cut builds `files`, and bytecode
    is decided by the ONE §16.16(r)/A26 predicate `hashing.is_bytecode` — never by a directory,
    so `__pycache__/oracle.json` IS reported as an extra while `__pycache__/x.cpython-312.pyc`
    and its temporary `x.cpython-312.pyc.140213` form are not.
    """
    unmanifested: list[str] = []
    for path in sorted(release_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(release_dir).as_posix()
        if relative in (MANIFEST_FILENAME, AUDIT_FILENAME):
            continue
        if relative.split("/", 1)[0] == REPORTS_DIRNAME:
            continue
        if is_bytecode(path):
            continue
        if relative not in files:
            unmanifested.append(relative)
    return unmanifested


def _verify_frozen_extras(
    release_dir: Path, manifest: Mapping[str, object], files: Mapping[str, Mapping[str, object]]
) -> None:
    """Frozen-only checks: the runner hash (R7) and the absence of unmanifested files (§16.6)."""
    frozen_runner = _require(manifest, "runner_sha256", str)
    actual_runner = runner_sha256()
    if actual_runner != str(frozen_runner).lower():
        raise RunnerHashMismatchError(
            "runner_sha256 differs from the value this frozen release froze — refusing to run"
        )
    extras = _unmanifested_files(release_dir, files)
    if extras:
        raise ReleaseLockError(
            f"frozen release carries {len(extras)} file(s) the manifest does not name"
        )


def verify_release_visible(release_dir: Path, *, expect_lock: str | None) -> ReleaseInfo:
    """Stage 1 of the lock (§4.3): verify the manifest and every VISIBLE input it names.

    Recomputes the manifest's own sha256 (comparing it against `expect_lock` when given), then
    the criteria annex, the protocol stub, every declared schema and every `visibility:
    "visible"` file entry. On a frozen release it additionally recomputes `runner_sha256` and
    refuses any file under the release directory the manifest does not name — except the four
    §16.6 exemptions the cut never manifests either (the manifest, `audit.jsonl`, `reports/**`
    and bytecode). No hidden path is opened, stat-ed or resolved.

    Args:
        release_dir: the release directory (`<gold root>/releases/<name>/`).
        expect_lock: the lock the caller expects, `sha256:<hex>` or bare hex in either case, or
            None to accept and report the computed lock.

    Returns:
        The verified `ReleaseInfo` with `hidden_files_verified == 0`.

    Raises:
        ReleaseLockError: any manifest, file or layout mismatch.
        RunnerHashMismatchError: a frozen release froze a different `runner_sha256`.
    """
    manifest_path = release_dir / MANIFEST_FILENAME
    lock = compute_lock(manifest_path)
    if expect_lock is not None and normalize_lock(expect_lock) != lock:
        raise ReleaseLockError("release lock differs from the expected lock — refusing to run")
    manifest = _load_manifest(manifest_path)
    state = _require(manifest, "release_state", str)
    if state not in ("draft", "frozen"):
        raise ReleaseLockError(f"release_state {state!r} is neither 'draft' nor 'frozen'")
    name = str(_require(manifest, "release_name", str))
    criteria_path = release_dir / CRITERIA_FILENAME
    _verify_file(criteria_path, manifest.get("criteria_sha256"), None, "criteria annex")
    _verify_file(
        release_dir / PROTOCOL_FILENAME, manifest.get("protocol_sha256"), None, "protocol stub"
    )
    _verify_schemas(release_dir, manifest)
    files = _files_entries(manifest)
    verified = _verify_visible_files(release_dir, files)
    if state == "frozen":
        _verify_frozen_extras(release_dir, manifest, files)
    return ReleaseInfo(
        path=release_dir,
        name=name,
        state="frozen" if state == "frozen" else "draft",
        lock_sha256=lock,
        manifest=manifest,
        criteria_path=criteria_path,
        visible_files_verified=verified,
        hidden_files_verified=0,
    )


def _record_hidden_verified(release: ReleaseInfo, verified: int) -> None:
    """Set `hidden_files_verified` on a frozen dataclass after stage 2 ran.

    `verify_hidden_split` returns None by contract (§1.3) while `ReleaseInfo` is frozen and
    documents the counter as "0 until stage 2 ran" (§1.4) — the only way to honour both is to
    write the counter through `object.__setattr__` on the object stage 2 was handed.
    """
    object.__setattr__(release, "hidden_files_verified", verified)


def hidden_entries(
    manifest: Mapping[str, object], split: str, sets: Sequence[str]
) -> dict[str, Mapping[str, object]]:
    """Return the manifest's hidden `files` entries for `split` restricted to `sets`.

    A hidden path is `hidden/<split>/<SET>/<file>`; entries of any other split, or of a set the
    caller did not name, are never returned and therefore never opened.
    """
    wanted = set(sets)
    selected: dict[str, Mapping[str, object]] = {}
    for relative, entry in _files_entries(manifest).items():
        if entry["visibility"] != HIDDEN:
            continue
        parts = relative.split("/")
        if len(parts) < 4 or parts[0] != HIDDEN or parts[1] != split:
            continue
        if parts[2] in wanted:
            selected[relative] = entry
    return selected


def verify_hidden_split(
    release: ReleaseInfo,
    hidden_root: Path,
    split: Literal["test", "validation"],
    sets: Sequence[str],
) -> None:
    """Stage 2 of the lock (§4.3): verify exactly the named split's files for the named sets.

    Each manifest path `hidden/<split>/<SET>/<file>` resolves to
    `<hidden root>/releases/<release name>/<split>/<SET>/<file>` — the `hidden/` prefix is
    stripped (§16.6). Files of the other split, and of sets not named here, are never opened.

    Args:
        release: the `ReleaseInfo` stage 1 returned.
        hidden_root: the hidden root the split lives under.
        split: `test` (checkpoint) or `validation` (the founder run).
        sets: the SETs whose hidden files this run is authorized to read.

    Raises:
        ReleaseLockError: a selected hidden file is absent or differs from the manifest.
    """
    if split not in ("test", "validation"):
        raise ReleaseLockError(f"unknown hidden split {split!r}")
    entries = hidden_entries(release.manifest, split, sets)
    verified = 0
    for relative, entry in sorted(entries.items()):
        resolved = hidden_root / RELEASES_DIRNAME / release.name / relative[len(HIDDEN_PREFIX) :]
        _verify_file(resolved, entry.get("sha256"), entry.get("bytes"), f"hidden file {relative}")
        verified += 1
    _record_hidden_verified(release, verified)
