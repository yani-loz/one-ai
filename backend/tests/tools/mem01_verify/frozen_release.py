"""
Role: Builder for a SYNTHETIC FROZEN release (contract §16.6/§16.10/§16.13) derived from a draft
      release the instrument cut: the draft directory is copied under a fresh gold root, its
      state is flipped to `frozen`, the frozen-only fields are added, visible optimization files
      and hidden split files of `{"gold_id"}` lines are written with DISTINCT ids per split, the
      per-split `records` object is filled, and `files` is rebuilt over EVERY regular file — so
      the CLI-level frozen paths (runner-hash refusal, the Stage A `no scorable hidden set`
      refusal of `--checkpoint` / `--validation`) can be sealed end to end without `--freeze`.
Used by: cli_fixtures.frozen_release (session-cached variants), scenario_fixtures,
      test_verify_step1_frozen.py, test_verify_step1_validation.py; proven by
      test_oracle_helpers.py over a synthetic_release draft.
Depends on: tests.tools.mem01_verify.reference (hashes, gold-id lines), .synthetic_release
      (hidden file + decoy writer); stdlib.
Key invariants:
  - `files` names every regular file under the release except `dataset.manifest.json`,
    `audit.jsonl` and `reports/**`; `records` of a `.jsonl` entry is its line count (§16.6).
  - The hidden files live ONLY in the split the variant selects; the manifest carries one BOGUS
    entry per set for the other split (absent file, made-up hash). With `tamper_hidden` the
    selected split's entries carry a flipped hash too, so ANY hidden open surfaces as a lock
    error — which is how a run that aborts for another reason proves it never opened one.
  - `records[<SET>]` is the §16.13 per-split object; optimization ids and hidden ids differ.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.synthetic_release import write_hidden_with_decoy

H_SETS = ("QS", "NF", "LANG", "RET")
SPLITS = ("optimization", "test", "validation")
BOGUS_SHA256 = "f" * 64
HiddenSplit = Literal["test", "validation"]


@dataclass(frozen=True)
class FrozenRelease:
    """Paths and ids of a built synthetic frozen release."""

    gold_root: Path
    hidden_root: Path
    path: Path
    name: str
    manifest_path: Path
    ledger_path: Path
    audit_path: Path
    hidden_split: str
    gold_ids: dict[str, tuple[str, ...]]
    optimization_ids: dict[str, tuple[str, ...]]

    def manifest(self) -> dict:
        return reference.read_json(self.manifest_path)

    def write_manifest(self, manifest: dict) -> None:
        write_manifest(self.manifest_path, manifest)

    def lock_sha256(self) -> str:
        """§4.3: the lock is the sha256 of the manifest bytes as they lie on disk."""
        return reference.sha256_hex(self.manifest_path.read_bytes())

    def hidden_report_root(self) -> Path:
        """Where hidden runs write their reports (§3.1 `--report-dir`)."""
        return self.hidden_root / "releases" / self.name / "reports"

    def hidden_file(self, set_name: str) -> Path:
        """The resolved path of `hidden/<split>/<set>/part0.jsonl` (§16.6)."""
        return (
            self.hidden_root
            / "releases"
            / self.name
            / self.hidden_split
            / set_name
            / ("part0.jsonl")
        )


def write_manifest(path: Path, manifest: dict) -> None:
    """Write a manifest with sorted keys, indent 1, UTF-8 (the lock covers these bytes)."""
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8"
    )


def _entry(payload: bytes, visibility: str, relative: str) -> dict:
    return {
        "sha256": reference.sha256_hex(payload),
        "bytes": len(payload),
        "records": payload.count(b"\n") if relative.endswith(".jsonl") else 0,
        "visibility": visibility,
    }


def manifest_files_for(release: Path) -> dict[str, dict]:
    """§16.6: an entry for every regular file except the manifest, audit.jsonl and reports/**."""
    files: dict[str, dict] = {}
    for path in sorted(release.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(release).as_posix()
        if relative in ("dataset.manifest.json", "audit.jsonl") or relative.startswith("reports/"):
            continue
        if "__pycache__" in relative:
            continue
        files[relative] = _entry(path.read_bytes(), "visible", relative)
    return files


def _split_records(
    hidden_split: str, optimization: tuple[str, ...], hidden: tuple[str, ...]
) -> dict[str, list[str]]:
    records = {split: [] for split in SPLITS}
    records["optimization"] = sorted(optimization)
    records[hidden_split] = sorted(hidden)
    return records


def build_frozen_release(
    source_release: Path,
    root: Path,
    *,
    runner_sha256: str,
    hidden_split: HiddenSplit,
    records_per_set: int = 2,
    bogus_other_split: bool = True,
    tamper_hidden: bool = False,
) -> FrozenRelease:
    """Copy `source_release` (a draft layout) to `<root>/gold/releases/<name>/` as frozen.

    The hidden root is `<root>/hidden`; the ledger `<root>/gold/hidden_budget.jsonl` starts
    empty (the runner constructs `HiddenBudget` with `create_if_missing=False`, §16.2); the
    release audit starts empty. Records of the non-H sets are copied from the draft verbatim.
    """
    source_manifest = reference.read_json(source_release / "dataset.manifest.json")
    name = source_manifest["release_name"]
    gold_root = root / "gold"
    hidden_root = root / "hidden"
    release = gold_root / "releases" / name
    shutil.copytree(
        source_release, release, ignore=shutil.ignore_patterns("reports", "__pycache__")
    )
    (release / "reports").mkdir(exist_ok=True)
    (release / "audit.jsonl").write_bytes(b"")
    (gold_root / "hidden_budget.jsonl").write_bytes(b"")
    manifest = dict(source_manifest)
    manifest["release_state"] = "frozen"
    manifest["runner_sha256"] = runner_sha256
    manifest["budget_ledger"] = "hidden_budget.jsonl"
    manifest["test_groups_provenance"] = {set_name: [] for set_name in manifest["sets"]}
    records = dict(manifest.get("records", {}))
    sets = {set_name: dict(entry) for set_name, entry in manifest.get("sets", {}).items()}
    gold_ids: dict[str, tuple[str, ...]] = {}
    optimization_ids: dict[str, tuple[str, ...]] = {}
    hidden_payloads: dict[str, bytes] = {}
    for set_name in H_SETS:
        visible_ids = tuple(
            f"{set_name.lower()}-opt-{index:03d}" for index in range(records_per_set)
        )
        hidden_ids = tuple(
            f"{set_name.lower()}-{hidden_split}-{index:03d}" for index in range(records_per_set)
        )
        visible = release / "data" / "optimization" / set_name / "part0.jsonl"
        visible.parent.mkdir(parents=True, exist_ok=True)
        visible.write_bytes(reference.gold_id_lines(visible_ids))
        payload = reference.gold_id_lines(hidden_ids)
        write_hidden_with_decoy(hidden_root, name, hidden_split, set_name, payload)
        hidden_payloads[set_name] = payload
        records[set_name] = _split_records(hidden_split, visible_ids, hidden_ids)
        sets[set_name] = {
            **sets.get(set_name, {}),
            "expected": len(visible_ids) + len(hidden_ids),
        }
        gold_ids[set_name] = hidden_ids
        optimization_ids[set_name] = visible_ids
    files = manifest_files_for(release)
    other = "validation" if hidden_split == "test" else "test"
    for set_name in H_SETS:
        relative = f"hidden/{hidden_split}/{set_name}/part0.jsonl"
        entry = _entry(hidden_payloads[set_name], "hidden", relative)
        if tamper_hidden:
            entry["sha256"] = flip_one_hex_digit(entry["sha256"])
        files[relative] = entry
        if bogus_other_split:
            files[f"hidden/{other}/{set_name}/part0.jsonl"] = {
                "sha256": BOGUS_SHA256,
                "bytes": 1,
                "records": 1,
                "visibility": "hidden",
            }
    manifest["files"] = files
    manifest["records"] = records
    manifest["sets"] = sets
    manifest_path = release / "dataset.manifest.json"
    write_manifest(manifest_path, manifest)
    return FrozenRelease(
        gold_root=gold_root,
        hidden_root=hidden_root,
        path=release,
        name=name,
        manifest_path=manifest_path,
        ledger_path=gold_root / "hidden_budget.jsonl",
        audit_path=release / "audit.jsonl",
        hidden_split=hidden_split,
        gold_ids=gold_ids,
        optimization_ids=optimization_ids,
    )


def flip_one_hex_digit(digest: str) -> str:
    """A different 64-hex string that differs from `digest` in its last character."""
    return digest[:-1] + ("0" if digest[-1] != "0" else "1")
