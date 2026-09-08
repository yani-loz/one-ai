"""
Role: Builder for a minimal SYNTHETIC release directory (contract §4.1/§4.2/§16.6/§16.10/§16.13
      shapes) under a temporary gold root — draft or frozen, with a manifest whose hashes are
      consistent with the files it names — so lock, roster and hidden-split behaviour can be
      sealed without a real cut and without a real hidden root.
Used by: test_lock.py, test_roster.py; proven by test_oracle_helpers.py.
Depends on: tests.tools.mem01_verify.reference (hashes, gold-id lines), the draft criteria annex
      (copied in).
Key invariants:
  - `files` names EVERY regular file under the release except the manifest, `audit.jsonl` and
    `reports/**` (§16.6), so a frozen build verifies and an `extra_unmanifested` file is the
    only unnamed one.
  - `records[<SET>]` is the §16.13 per-split object `{optimization, test, validation}` of sorted
    gold ids; a test may replace one with the `{by_digest, roster_counts}` corpus form.
  - Hidden entries are namespaced `hidden/<split>/<set>/…` and resolve under
    `<hidden root>/releases/<name>/<split>/<set>/…` — the `hidden/` prefix stripped (§16.6); a
    DECOY with different bytes sits at the UNSTRIPPED location, so a resolver that keeps the
    prefix hashes wrong. Every data file holds `{"gold_id": …}` lines (§16.10).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from tests.tools.mem01_verify import reference

RELEASE_NAME = "step1-gold-v1"
SETS = (
    "QS",
    "CH",
    "NF",
    "LANG",
    "RET",
    "IDEM",
    "VIS",
    "ERASE",
    "COV",
    "FID",
    "THR",
    "TIME",
    "IDENT",
    "RED",
    "ATTR",
    "SNAP",
    "EMB",
)
SPLITS = ("optimization", "test", "validation")
CRITERIA_ANNEX = (
    Path(__file__).resolve().parents[3]
    / "tools"
    / "mem01_verify"
    / "release"
    / ("criteria.step1.v1.yaml")
)
PLACEHOLDER_HEX = "0" * 64
ORG_ID = UUID("00000000-0000-4000-8000-0000000000aa")
DECOY_PAYLOAD = b'{"gold_id": "decoy-at-the-unstripped-path"}\n'


def empty_records() -> dict[str, list[str]]:
    """The §16.13 per-split roster object with every split empty."""
    return {split: [] for split in SPLITS}


@dataclass(frozen=True)
class SyntheticRelease:
    """Paths of a built synthetic release."""

    gold_root: Path
    hidden_root: Path
    path: Path
    name: str
    manifest_path: Path

    def manifest(self) -> dict:
        return reference.read_json(self.manifest_path)

    def write_manifest(self, manifest: dict) -> None:
        self.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8"
        )

    def hidden_file(self, split: str, set_name: str) -> Path:
        """Where `hidden/<split>/<set>/part0.jsonl` resolves (§16.6, prefix stripped)."""
        return self.hidden_root / "releases" / self.name / split / set_name / "part0.jsonl"

    def decoy_file(self, split: str, set_name: str) -> Path:
        """The UNSTRIPPED location a wrong resolver would open."""
        return (
            self.hidden_root / "releases" / self.name / "hidden" / split / set_name / "part0.jsonl"
        )

    def write_visible_file(self, relative: str, payload: bytes) -> None:
        """Write a visible data file and re-manifest its entry so stage 1 stays consistent."""
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        manifest = self.manifest()
        manifest["files"][relative] = file_entry(
            payload, "visible", jsonl=relative.endswith(".jsonl")
        )
        self.write_manifest(manifest)


def file_entry(payload: bytes, visibility: str, *, jsonl: bool) -> dict:
    """A §4.2 `files` entry: sha256, byte length, record count (lines of a JSONL), visibility."""
    return {
        "sha256": reference.sha256_hex(payload),
        "bytes": len(payload),
        "records": payload.count(b"\n") if jsonl else 0,
        "visibility": visibility,
    }


def write_hidden_with_decoy(
    hidden_root: Path, name: str, split: str, set_name: str, payload: bytes
) -> Path:
    """Write the real hidden file at the resolved path and a decoy at the unstripped path."""
    target = hidden_root / "releases" / name / split / set_name / "part0.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    decoy = hidden_root / "releases" / name / "hidden" / split / set_name / "part0.jsonl"
    decoy.parent.mkdir(parents=True, exist_ok=True)
    decoy.write_bytes(DECOY_PAYLOAD)
    return target


def build_release(
    root: Path,
    *,
    state: str = "draft",
    runner_sha256: str,
    name: str = RELEASE_NAME,
    hidden_records: dict[str, list[str]] | None = None,
    hidden_split: str = "test",
    optimization_records: dict[str, list[str]] | None = None,
    extra_unmanifested: dict[str, bytes] | None = None,
) -> SyntheticRelease:
    """Create `<root>/gold/releases/<name>/` (+ a hidden root) and return its descriptor.

    `optimization_records` maps a SET to the gold ids of the visible
    `data/optimization/<set>/part0.jsonl`; `hidden_records` maps a SET to the gold ids of
    `hidden/<hidden_split>/<set>/part0.jsonl` (written under the hidden root, with a decoy at
    the unstripped path). Both become `records[<set>][<split>]`. `extra_unmanifested` writes
    files under the release that the manifest does NOT name (tolerated on a draft, refused on
    a frozen release).
    """
    gold_root = root / "gold"
    hidden_root = root / "hidden"
    release = gold_root / "releases" / name
    (release / "schemas").mkdir(parents=True)
    (release / "data" / "optimization").mkdir(parents=True)
    (release / "reports" / "oracle_run").mkdir(parents=True)
    (release / "reports" / "oracle_run" / "note.txt").write_bytes(b"reports are never manifested")
    (release / "audit.jsonl").write_bytes(b"")
    (gold_root / "hidden_budget.jsonl").write_bytes(b"")
    criteria = CRITERIA_ANNEX.read_bytes()
    (release / "criteria.step1.v1.yaml").write_bytes(criteria)
    protocol = b"# PROTOCOL v1 (synthetic stub, non-PII)\n"
    (release / "PROTOCOL.v1.md").write_bytes(protocol)
    schema = json.dumps(
        {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"}
    ).encode("utf-8")
    (release / "schemas" / "record_core.schema.json").write_bytes(schema)
    files: dict[str, dict] = {
        "criteria.step1.v1.yaml": file_entry(criteria, "visible", jsonl=False),
        "PROTOCOL.v1.md": file_entry(protocol, "visible", jsonl=False),
        "schemas/record_core.schema.json": file_entry(schema, "visible", jsonl=False),
    }
    records: dict[str, dict[str, list[str]]] = {set_name: empty_records() for set_name in SETS}
    sets: dict[str, dict] = {set_name: {"expected": 0, "evidence": []} for set_name in SETS}
    for set_name, gold_ids in (optimization_records or {}).items():
        payload = reference.gold_id_lines(gold_ids)
        relative = f"data/optimization/{set_name}/part0.jsonl"
        target = release / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        files[relative] = file_entry(payload, "visible", jsonl=True)
        records[set_name]["optimization"] = sorted(gold_ids)
        sets[set_name]["expected"] += len(gold_ids)
        sets[set_name]["evidence"].append("H-optimization")
    for set_name, gold_ids in (hidden_records or {}).items():
        payload = reference.gold_id_lines(gold_ids)
        files[f"hidden/{hidden_split}/{set_name}/part0.jsonl"] = file_entry(
            payload, "hidden", jsonl=True
        )
        write_hidden_with_decoy(hidden_root, name, hidden_split, set_name, payload)
        records[set_name][hidden_split] = sorted(gold_ids)
        sets[set_name]["expected"] += len(gold_ids)
        sets[set_name]["evidence"].append(f"H-{hidden_split}")
    for relative, payload in (extra_unmanifested or {}).items():
        target = release / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    manifest = {
        "release_name": name,
        "release_state": state,
        "cut_at": "2026-09-06T12:00:00+00:00",
        "runner_sha256": runner_sha256,
        "criteria_sha256": reference.sha256_hex(criteria),
        "protocol_sha256": reference.sha256_hex(protocol),
        "schemas": {"schemas/record_core.schema.json": reference.sha256_hex(schema)},
        "files": files,
        "records": records,
        "corpus": {
            "org_id": str(ORG_ID),
            "emails": 0,
            "attachments": 0,
            "corpus_digest": PLACEHOLDER_HEX,
            "text_digest": PLACEHOLDER_HEX,
            "snapshot_manifest_sha256": PLACEHOLDER_HEX,
        },
        "fixtures_digest": PLACEHOLDER_HEX,
        "cells_out_of_scope": [],
        "sets": sets,
    }
    if state == "frozen":
        manifest["budget_ledger"] = "hidden_budget.jsonl"
        manifest["test_groups_provenance"] = {set_name: [] for set_name in SETS}
    built = SyntheticRelease(
        gold_root=gold_root,
        hidden_root=hidden_root,
        path=release,
        name=name,
        manifest_path=release / "dataset.manifest.json",
    )
    built.write_manifest(manifest)
    return built


def flip_one_hex_digit(digest: str) -> str:
    """A different 64-hex string that differs from `digest` in its last character."""
    return digest[:-1] + ("0" if digest[-1] != "0" else "1")
