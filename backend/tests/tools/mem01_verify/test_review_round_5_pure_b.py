"""
Role: Seals fix-registry rows A38, A39 / contract §16.17(k) on the roster — `duplicate` is the
      SUM of both sides (a gold id the manifest lists twice counts 1; listed twice AND present
      twice counts 2) and a data line that is not valid UTF-8 is `malformed` PER LINE (the valid
      lines around it are still `present`, so whole-file rejection cannot pass), both reported
      by count and never by id.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.roster, .lock, .exceptions (imported inside each test through the
      `instrument` loader); tests.tools.mem01_verify.reference and .synthetic_release.
Key invariants:
  - Every edited data file is re-manifested (`write_visible_file`) so stage 1 — which hashes
    bytes and never decodes — still passes and the red is the roster's alone.
  - The undecodable lines carry ids OUTSIDE the roster where they must not add to `present`, and
    the roster's own id where a strict per-line decode must count it `missing`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.tools.mem01_verify import reference, synthetic_release
from tests.tools.mem01_verify.conftest import InstrumentLoader

QS_IDS = ["qs-oracle-0001", "qs-oracle-0002", "qs-oracle-0003"]
NF_IDS = ["nf-oracle-0001", "nf-oracle-0002"]
QS_FILE = "data/optimization/QS/part0.jsonl"
NF_FILE = "data/optimization/NF/part0.jsonl"


def _undecodable(gold_id: str) -> bytes:
    return b'{"gold_id": "' + gold_id.encode() + b'", "note": "\xff"}\n'


def _release(tmp_path: Path, runner_folder: Path) -> object:
    return synthetic_release.build_release(
        tmp_path,
        runner_sha256=reference.merkle_sha256_reference(runner_folder),
        optimization_records={"QS": QS_IDS, "NF": NF_IDS},
    )


def _verify(instrument: InstrumentLoader, built: object) -> object:
    info = instrument("lock").verify_release_visible(built.path, expect_lock=None)  # type: ignore[attr-defined]
    return instrument("roster").verify_roster(info, split="optimization", hidden_root=None)


def _mismatch(instrument: InstrumentLoader, built: object) -> object:
    with pytest.raises(instrument("exceptions").RosterMismatchError) as caught:
        _verify(instrument, built)
    return caught.value


def _list_twice(built: object, set_name: str, gold_id: str, ids: list[str]) -> None:
    manifest = built.manifest()  # type: ignore[attr-defined]
    manifest["records"][set_name]["optimization"] = [gold_id, *ids]
    built.write_manifest(manifest)  # type: ignore[attr-defined]


def test_a_consistent_roster_is_still_ok(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path
) -> None:
    built = _release(tmp_path, runner_folder)

    report = _verify(instrument, built)

    assert report.ok is True and report.sets["QS"]["present"] == 3  # type: ignore[attr-defined]


def test_a_gold_id_listed_twice_by_the_manifest_counts_one_duplicate_without_being_named(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path
) -> None:
    built = _release(tmp_path, runner_folder)
    _list_twice(built, "NF", "nf-oracle-0001", NF_IDS)

    error = _mismatch(instrument, built)

    assert error.counts["NF"]["duplicate"] == 1, dict(error.counts["NF"])  # type: ignore[attr-defined]
    assert "nf-oracle-0001" not in str(error)


def test_duplicates_on_both_sides_are_summed(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path
) -> None:
    built = _release(tmp_path, runner_folder)
    _list_twice(built, "NF", "nf-oracle-0001", NF_IDS)  # one expected-side duplicate
    built.write_visible_file(  # type: ignore[attr-defined]
        NF_FILE,
        reference.gold_id_lines([*NF_IDS, "nf-oracle-0002"]),  # one present-side duplicate
    )

    error = _mismatch(instrument, built)

    assert error.counts["NF"]["duplicate"] == 2, dict(error.counts["NF"])  # type: ignore[attr-defined]
    assert not any(gold_id in str(error) for gold_id in NF_IDS)


def test_a_data_line_that_is_not_valid_utf8_is_malformed_by_count(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path
) -> None:
    built = _release(tmp_path, runner_folder)
    payload = reference.gold_id_lines(QS_IDS[1:]) + _undecodable("qs-oracle-0001")
    built.write_visible_file(QS_FILE, payload)  # type: ignore[attr-defined]

    error = _mismatch(instrument, built)

    counts = dict(error.counts["QS"])  # type: ignore[attr-defined]
    assert counts["malformed"] == 1, counts
    assert counts["present"] == 2 and counts["missing"] == 1, counts
    assert "qs-oracle-0001" not in str(error)


def test_two_undecodable_lines_between_valid_records_are_malformed_per_line(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path
) -> None:
    built = _release(tmp_path, runner_folder)
    payload = (
        reference.gold_id_lines(QS_IDS[:1])
        + _undecodable("qs-oracle-0091")
        + reference.gold_id_lines(QS_IDS[1:2])
        + _undecodable("qs-oracle-0092")
        + reference.gold_id_lines(QS_IDS[2:])
    )
    built.write_visible_file(QS_FILE, payload)  # type: ignore[attr-defined]

    error = _mismatch(instrument, built)

    counts = dict(error.counts["QS"])  # type: ignore[attr-defined]
    assert counts["malformed"] == 2, counts
    assert counts["present"] == 3, counts  # whole-file rejection would leave nothing present
    assert counts["missing"] == 0 and counts["unexpected"] == 0, counts
