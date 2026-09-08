"""
Role: Builds and writes `dataset.manifest.json` — the §4.2 release manifest whose bytes the
      §4.3 lock hashes. It walks the release directory for the `files` table, derives the
      per-SET rosters (§16.13) and set summaries from the criteria annex, and renders the
      manifest with a canonical, deterministic JSON encoding so a re-cut over an unchanged
      release differs only in `cut_at`.
Used by: tools.mem01_verify.release (the `cut` subcommand); `is_manifested` is sealed
      directly by tests/tools/mem01_verify/test_bytecode_suffix_chain.py.
Depends on: tools.mem01_verify.criteria (CriteriaFile / Criterion), .hashing (sha256_file,
      is_bytecode), .exceptions (ReleaseLockError).
Key invariants:
  - `files` names EVERY regular file under the release except `dataset.manifest.json`,
    `audit.jsonl`, anything under `reports/` and bytecode (§16.6, by the §16.16(r)/A26
    suffix-chain predicate `hashing.is_bytecode`); NO directory is excluded, so a non-bytecode
    file under a `__pycache__` directory is manifested like any other. Paths are posix and
    relative to the release directory.
  - The manifest carries hashes and counts only — never a case id, an address or a body.
  - The rendering is byte-deterministic: sorted keys, `ensure_ascii=False`, indent 1, UTF-8,
    no trailing newline, so `sha256(manifest bytes)` is a stable lock.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from tools.mem01_verify.criteria import CriteriaFile
from tools.mem01_verify.exceptions import ReleaseLockError
from tools.mem01_verify.hashing import is_bytecode, sha256_file

MANIFEST_NAME = "dataset.manifest.json"
AUDIT_NAME = "audit.jsonl"
LEDGER_NAME = "hidden_budget.jsonl"
CRITERIA_NAME = "criteria.step1.v1.yaml"
PROTOCOL_NAME = "PROTOCOL.v1.md"
SCHEMAS_DIR = "schemas"
REPORTS_DIR = "reports"
SPLITS = ("optimization", "test", "validation")
_NEVER_MANIFESTED = frozenset({MANIFEST_NAME, AUDIT_NAME})
_HIDDEN_SPLIT_SOURCES = frozenset({"optimization", "test", "validation"})
_EVIDENCE_BY_SPLIT_SOURCE = {
    "corpus": "C",
    "fixtures": "F",
    "optimization": "H-optimization",
    "test": "H-test",
    "validation": "H-validation",
}


def render_manifest(manifest: Mapping[str, object]) -> bytes:
    """Return the canonical UTF-8 bytes of a manifest (the exact bytes the lock hashes).

    Args:
        manifest: The manifest mapping to encode.

    Returns:
        Sorted-key JSON with indent 1 and raw non-ASCII, encoded as UTF-8.
    """
    return json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8")


def write_manifest(release_dir: Path, manifest: Mapping[str, object]) -> Path:
    """Write `dataset.manifest.json` into `release_dir` and return its path."""
    path = release_dir / MANIFEST_NAME
    path.write_bytes(render_manifest(manifest))
    return path


def read_manifest(release_dir: Path) -> dict[str, object]:
    """Read the release's manifest; ReleaseLockError when it is absent or not a JSON object.

    Args:
        release_dir: The release directory holding `dataset.manifest.json`.

    Returns:
        The parsed manifest mapping.

    Raises:
        ReleaseLockError: The file is missing, unreadable, malformed, or not an object.
    """
    path = release_dir / MANIFEST_NAME
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseLockError(f"cannot read the release manifest at {path}: {error}") from error
    if not isinstance(document, dict):
        raise ReleaseLockError(f"the release manifest at {path} is not a JSON object")
    return document


def is_manifested(relative: str) -> bool:
    """True when a release-relative posix path belongs in the manifest's `files` table (§16.6).

    §16.6 excludes exactly three things: `dataset.manifest.json` (whose bytes the lock hashes),
    the release `audit.jsonl`, and everything under `reports/`. Beyond those, the ONLY further
    exclusion is bytecode, decided by the §16.16(r)/A26 suffix-chain predicate — no directory
    is excluded, so `__pycache__/oracle.json` is manifested while
    `__pycache__/x.cpython-312.pyc.140213` is not.

    Args:
        relative: A release-relative posix path.

    Returns:
        True when the path belongs in `files`.
    """
    if relative in _NEVER_MANIFESTED or relative.startswith(f"{REPORTS_DIR}/"):
        return False
    return not is_bytecode(PurePosixPath(relative))


def count_records(path: Path, relative: str) -> int:
    """Return the record count of a manifested file: JSONL lines, or 0 for any other format."""
    if not relative.endswith(".jsonl"):
        return 0
    with path.open("rb") as handle:
        return sum(1 for line in handle if line.strip())


def manifest_files(release_dir: Path) -> dict[str, dict[str, object]]:
    """Return the §4.2 `files` table over every visible regular file of the release.

    Args:
        release_dir: The release directory to walk recursively.

    Returns:
        A mapping of release-relative posix path to `{sha256, bytes, records, visibility}`,
        with `visibility` always `"visible"` — Stage A cuts no hidden entries.
    """
    files: dict[str, dict[str, object]] = {}
    for path in sorted(release_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(release_dir).as_posix()
        if not is_manifested(relative):
            continue
        files[relative] = {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
            "records": count_records(path, relative),
            "visibility": "visible",
        }
    return files


def _uses_hidden_splits(criteria: CriteriaFile, gate: str) -> bool:
    """True when the gate draws evidence from a gold split (its roster lists gold ids)."""
    return any(
        criterion.split_source in _HIDDEN_SPLIT_SOURCES for criterion in criteria.by_gate[gate]
    )


def _uses_corpus(criteria: CriteriaFile, gate: str) -> bool:
    """True when the gate draws evidence from the corpus (its roster is a digest shortcut)."""
    return any(criterion.split_source == "corpus" for criterion in criteria.by_gate[gate])


def roster_records(
    criteria: CriteriaFile, *, corpus_digest: str, roster_counts: Mapping[str, int]
) -> dict[str, dict[str, object]]:
    """Return the §16.13 `records` table: one roster per SET.

    A SET whose evidence is the corpus and that holds no gold split carries the
    `{by_digest, roster_counts}` shortcut — the corpus digest establishes complete row-id
    equality for every table it covers (§5.2). Every other SET carries the per-split object of
    sorted `gold_id` lists, empty in Stage A because no record has been labeled yet.

    Args:
        criteria: The loaded annex; its gate index names the 17 SETs.
        corpus_digest: The digest of the measured database state.
        roster_counts: Per-table row counts taken in the same snapshot.

    Returns:
        A mapping of SET name to its roster object.
    """
    records: dict[str, dict[str, object]] = {}
    for gate in criteria.by_gate:
        if _uses_corpus(criteria, gate) and not _uses_hidden_splits(criteria, gate):
            records[gate] = {
                "by_digest": corpus_digest,
                "roster_counts": dict(sorted(roster_counts.items())),
            }
        else:
            records[gate] = {split: [] for split in SPLITS}
    return records


def set_summaries(criteria: CriteriaFile) -> dict[str, dict[str, object]]:
    """Return the §4.2 `sets` table: per SET the expected record count and its evidence kinds.

    Args:
        criteria: The loaded annex.

    Returns:
        A mapping of SET name to `{expected, evidence}`; `expected` is 0 in Stage A (no record
        has been drawn) and `evidence` lists the distinct evidence letters the SET's criteria
        declare, sorted.
    """
    summaries: dict[str, dict[str, object]] = {}
    for gate, gate_criteria in criteria.by_gate.items():
        evidence = sorted(
            {_EVIDENCE_BY_SPLIT_SOURCE[criterion.split_source] for criterion in gate_criteria}
        )
        summaries[gate] = {"expected": 0, "evidence": evidence}
    return summaries


def build_manifest(
    release_dir: Path,
    *,
    name: str,
    cut_at: str,
    runner_digest: str,
    criteria: CriteriaFile,
    corpus: Mapping[str, object],
    roster_counts: Mapping[str, int],
    fixtures_digest: str,
) -> dict[str, object]:
    """Assemble the complete §4.2 draft manifest for a release whose files are already on disk.

    Args:
        release_dir: The release directory (already populated by the cut).
        name: The release name (`step1-gold-v1` by default).
        cut_at: ISO-8601 UTC timestamp of this cut — the only field a re-cut may move.
        runner_digest: `runner_sha256()` at cut time.
        criteria: The loaded annex, used for the roster shapes and set summaries.
        corpus: The §4.2 corpus section (org, counts, digests, snapshot manifest hash).
        roster_counts: Per-table row counts for the by-digest rosters.
        fixtures_digest: The fixtures package digest at cut time.

    Returns:
        The manifest mapping, ready for `write_manifest`.
    """
    schemas_dir = release_dir / SCHEMAS_DIR
    schemas = {
        path.relative_to(release_dir).as_posix(): sha256_file(path)
        for path in sorted(schemas_dir.rglob("*.json"))
        if path.is_file()
    }
    return {
        "release_name": name,
        "release_state": "draft",
        "cut_at": cut_at,
        "runner_sha256": runner_digest,
        "criteria_sha256": sha256_file(release_dir / CRITERIA_NAME),
        "protocol_sha256": sha256_file(release_dir / PROTOCOL_NAME),
        "schemas": schemas,
        "files": manifest_files(release_dir),
        "records": roster_records(
            criteria, corpus_digest=str(corpus["corpus_digest"]), roster_counts=roster_counts
        ),
        "corpus": dict(corpus),
        "fixtures_digest": fixtures_digest,
        "cells_out_of_scope": [],
        "sets": set_summaries(criteria),
        "budget_ledger": LEDGER_NAME,
    }
