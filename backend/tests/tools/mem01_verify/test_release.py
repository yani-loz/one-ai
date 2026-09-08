"""
Role: Seals the release tooling of contract §4.1/§4.2/§4.4 — the draft layout the cut produces
      under a temporary gold root over the probe corpus, every manifest field and its hashes,
      no personal data in the manifest, the idempotent re-cut (byte-identical except `cut_at`),
      the in-process `cut_draft_release`, and the Stage A refusal of `--freeze`.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.release, .lock, .snapshot, .fixtures.digest, .db (imported inside
      each test); conftest.draft_release / baseline_pair (session-cached child-process runs).
Key invariants:
  - Every hash in the manifest is recomputed here from the file bytes with hashlib.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import (
    GATE_NAMES,
    SESSION_LOOP,
    BaselinePairFactory,
    CliRunner,
    DraftReleaseFactory,
    InstrumentLoader,
    ProbeCorpusFactory,
    cut_arguments,
)

LAYOUT_FILES = (
    "dataset.manifest.json",
    "criteria.step1.v1.yaml",
    "PROTOCOL.v1.md",
    "sample_seed.json",
    "audit.jsonl",
)
INSTRUMENT_DIRS = ("census", "leakage", "lang_bootstrap")
INSTRUMENT_FILES = frozenset(
    {
        "census/census.json",
        "leakage/leakage_groups.jsonl",
        "leakage/leakage.summary.json",
        "lang_bootstrap/lang_bootstrap.jsonl",
    }
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@SESSION_LOOP
async def test_draft_cut_produces_the_layout_of_section_4_1(
    draft_release: DraftReleaseFactory,
    criteria_path: Path,
    runner_folder: Path,
) -> None:
    release = await draft_release()
    path = release.path

    for relative in LAYOUT_FILES:
        assert reference.is_file(path / relative), relative
    assert len(reference.rglob_files(path / "schemas", "*.json")) >= 20
    assert reference.is_empty_dir(path / "data" / "optimization")
    assert reference.rglob_files(path / "fixtures", "*")
    assert reference.file_size(path / "audit.jsonl") == 0
    assert reference.is_file(release.gold_root / "hidden_budget.jsonl")
    # §16.12: the cut creates the instrument directories EMPTY and never runs the instruments
    assert set(INSTRUMENT_DIRS) <= set(release.initial_dirs)
    assert not any(
        f.startswith(tuple(d + "/" for d in INSTRUMENT_DIRS)) for f in release.initial_files
    )
    assert reference.read_json(path / "sample_seed.json")["state"] == "unsampled"
    assert reference.read_bytes(path / "criteria.step1.v1.yaml") == reference.read_bytes(
        criteria_path
    )
    assert reference.read_bytes(path / "PROTOCOL.v1.md") == reference.read_bytes(
        runner_folder / "release" / "PROTOCOL.v1.md"
    )
    manifest = reference.read_json(path / "dataset.manifest.json")
    snapshot_manifest = (
        path / "snapshots" / manifest["corpus"]["text_digest"] / "snapshot.manifest.jsonl"
    )
    assert reference.is_file(snapshot_manifest)


@SESSION_LOOP
async def test_manifest_fields_and_hashes_are_consistent_with_the_files(
    instrument: InstrumentLoader,
    draft_release: DraftReleaseFactory,
    probe_corpus: ProbeCorpusFactory,
    runner_folder: Path,
) -> None:
    corpus = await probe_corpus()
    release = await draft_release()
    snapshot = instrument("snapshot")
    fixtures_digest = instrument("fixtures.digest").fixtures_digest()
    path = release.path
    manifest = reference.read_json(path / "dataset.manifest.json")

    assert manifest["release_name"] == release.name and manifest["release_state"] == "draft"
    datetime.fromisoformat(manifest["cut_at"])
    assert manifest["runner_sha256"] == reference.merkle_sha256_reference(runner_folder)
    assert manifest["criteria_sha256"] == _sha(path / "criteria.step1.v1.yaml")
    assert manifest["protocol_sha256"] == _sha(path / "PROTOCOL.v1.md")
    assert manifest["schemas"] and all(
        digest == _sha(path / relative) for relative, digest in manifest["schemas"].items()
    )
    assert manifest["files"], "the draft names no files"
    for relative, entry in manifest["files"].items():
        assert entry["visibility"] == "visible" and not relative.startswith("hidden/")
        assert entry["sha256"] == _sha(path / relative)
        assert entry["bytes"] == reference.file_size(path / relative)
        assert isinstance(entry["records"], int)
        assert not relative.startswith("reports/") and relative != "audit.jsonl"
    assert set(manifest["records"]) == set(GATE_NAMES) and set(manifest["sets"]) == set(GATE_NAMES)
    for set_name, roster in manifest["records"].items():  # §16.13 per-split rosters
        if "by_digest" in roster:
            assert set(roster) == {"by_digest", "roster_counts"}, set_name
            assert len(roster["by_digest"]) == 64
        else:
            assert set(roster) == {"optimization", "test", "validation"}, set_name
            assert all(value == sorted(value) for value in roster.values()), set_name
    corpus_section = manifest["corpus"]
    assert corpus_section["org_id"] == str(corpus.big.org_id)
    assert corpus_section["emails"] == corpus.big.email_count
    assert corpus_section["attachments"] == corpus.big.attachment_count
    snapshot_manifest = (
        path / "snapshots" / corpus_section["text_digest"] / "snapshot.manifest.jsonl"
    )
    assert corpus_section["text_digest"] == snapshot.text_digest_of(snapshot_manifest)
    assert corpus_section["snapshot_manifest_sha256"] == _sha(snapshot_manifest)
    assert len(corpus_section["corpus_digest"]) == 64
    assert manifest["fixtures_digest"] == fixtures_digest
    assert manifest["cells_out_of_scope"] == []


@SESSION_LOOP
async def test_manifest_and_cut_stdout_carry_no_personal_data(
    draft_release: DraftReleaseFactory,
    probe_corpus: ProbeCorpusFactory,
) -> None:
    corpus = await probe_corpus()
    release = await draft_release()
    manifest_text = (release.path / "dataset.manifest.json").read_text(encoding="utf-8")
    outputs = manifest_text + release.cut.stdout + release.cut.stderr

    assert "@" not in outputs
    assert not any(marker in outputs for marker in corpus.big.personal_markers)
    # positive control: the same markers ARE in the snapshot records (allowed, outside the repo)
    snapshot_dir = release.path / "snapshots"
    records = "".join(
        reference.read_text(p) for p in reference.rglob_files(snapshot_dir, "*.jsonl")
    )
    assert "OracleBodyText" in records


@SESSION_LOOP
async def test_re_cut_over_an_unchanged_release_is_byte_identical_except_cut_at(
    draft_release: DraftReleaseFactory,
) -> None:
    release = await draft_release()

    before = json.loads(release.initial_manifest.decode("utf-8"))
    after = json.loads(release.manifest_after_recut.decode("utf-8"))

    assert release.recut.exit_code == 0, release.recut.stderr[-1500:]
    assert datetime.fromisoformat(after["cut_at"]) >= datetime.fromisoformat(before["cut_at"])
    before.pop("cut_at")
    after.pop("cut_at")
    assert before == after


@SESSION_LOOP
async def test_instruments_write_the_three_outputs_and_the_next_cut_manifests_them(
    baseline_pair: BaselinePairFactory,
) -> None:
    pair = await baseline_pair()

    before = json.loads(pair.manifest_before.decode("utf-8"))
    after = reference.read_json(pair.release.path / "dataset.manifest.json")

    assert pair.instruments.exit_code == 0, pair.instruments.stderr[-1500:]
    assert pair.recut.exit_code == 0, pair.recut.stderr[-1500:]
    assert INSTRUMENT_FILES <= pair.files_after_instruments
    assert not (INSTRUMENT_FILES & set(before["files"]))  # absent before the instruments ran
    assert INSTRUMENT_FILES <= set(after["files"])  # re-manifested by the next cut
    for relative in INSTRUMENT_FILES:
        assert after["files"][relative]["sha256"] == _sha(pair.release.path / relative)
    assert before["corpus"] == after["corpus"]  # the corpus identity did not move


@SESSION_LOOP
async def test_freeze_is_refused_in_stage_a_and_changes_nothing(
    draft_release: DraftReleaseFactory,
    run_release_cli: CliRunner,
) -> None:
    release = await draft_release()
    before = reference.merkle_sha256_reference(release.gold_root)
    argv = [arg for arg in cut_arguments(release.gold_root, release.org_id) if arg != "--draft"]
    argv.insert(1, "--freeze")

    refused = await run_release_cli(argv, database=release.database, gold_root=release.gold_root)

    assert refused.exit_code != 0
    assert reference.merkle_sha256_reference(release.gold_root) == before
    assert reference.read_json(release.path / "dataset.manifest.json")["release_state"] == "draft"
    assert "@" not in refused.stdout + refused.stderr


@SESSION_LOOP
async def test_cut_draft_release_in_process_over_the_small_org(
    instrument: InstrumentLoader,
    probe_corpus: ProbeCorpusFactory,
    tmp_path: Path,
) -> None:
    corpus = await probe_corpus()
    db = instrument("db")
    release_module = instrument("release")
    lock = instrument("lock")
    small = corpus.small
    gold_root = tmp_path / "gold"

    async with db.readonly_corpus_snapshot(small.org_id, database=corpus.database) as conn:
        info = await release_module.cut_draft_release(gold_root, "oracle-small", small.org_id, conn)

    assert info.state == "draft" and info.name == "oracle-small"
    assert Path(info.path) == gold_root / "releases" / "oracle-small"
    assert info.lock_sha256 == lock.compute_lock(Path(info.path) / "dataset.manifest.json")
    assert info.manifest["corpus"]["emails"] == small.email_count
    assert info.manifest["corpus"]["attachments"] == small.attachment_count
    for directory in INSTRUMENT_DIRS:  # §16.12: created empty, never run by the cut
        assert reference.is_empty_dir(Path(info.path) / directory), directory
    assert reference.is_file(gold_root / "hidden_budget.jsonl")
    verified = lock.verify_release_visible(Path(info.path), expect_lock=info.lock_sha256)
    assert verified.lock_sha256 == info.lock_sha256
