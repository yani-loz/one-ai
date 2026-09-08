"""
Role: Seals the roster verification of contract §3.2 step 4 / §16.10 / §16.13 on a synthetic
      release with REAL gold data files — a consistent manifest yields a RosterReport over all 17
      sets; a same-count substitution, a duplicate id, an unexpected id, a listed id without a
      file, and a line whose `gold_id` is missing or empty are mismatches reported as COUNTS (an
      id never appears in the message); corpus rosters by digest need no file.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.roster, .lock, .exceptions (imported inside each test);
      tests.tools.mem01_verify.synthetic_release (proven by test_oracle_helpers.py).
Key invariants:
  - Every edited data file is re-manifested (`write_visible_file`), so stage 1 still passes and
    the mismatch is the roster's alone.
  - The record count token asserted in the message (7) is distinct from every other number the
    message could carry; the payload of RosterMismatchError is not determined (flagged), and
    the class raised for a malformed data line is only known to be a Mem01Error.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.tools.mem01_verify import reference, synthetic_release
from tests.tools.mem01_verify.conftest import GATE_NAMES, InstrumentLoader

REPORT_COUNTERS = {"expected", "present", "missing", "duplicate", "unexpected"}
QS_IDS = ["qs-oracle-0001", "qs-oracle-0002", "qs-oracle-0003"]
NF_IDS = ["nf-oracle-0001", "nf-oracle-0002"]
SEVEN_MISSING = [f"lang-oracle-{index:04d}" for index in range(1, 8)]


def _release(instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path) -> object:
    return synthetic_release.build_release(
        tmp_path,
        runner_sha256=reference.merkle_sha256_reference(runner_folder),
        optimization_records={"QS": QS_IDS, "NF": NF_IDS},
    )


def _verify(instrument: InstrumentLoader, built: object) -> object:
    info = instrument("lock").verify_release_visible(built.path, expect_lock=None)  # type: ignore[attr-defined]
    return instrument("roster").verify_roster(info, split="optimization", hidden_root=None)


def _refused(instrument: InstrumentLoader, built: object) -> None:
    """A malformed data line is refused; §16.10 does not name the class (flagged)."""
    with pytest.raises(instrument("exceptions").Mem01Error):
        _verify(instrument, built)


def _mismatch(instrument: InstrumentLoader, built: object) -> str:
    with pytest.raises(instrument("exceptions").RosterMismatchError) as caught:
        _verify(instrument, built)
    return str(caught.value)


def test_consistent_roster_over_real_gold_files_is_ok_for_all_sets(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path
) -> None:
    built = _release(instrument, tmp_path, runner_folder)

    report = _verify(instrument, built)

    assert report.ok is True
    assert set(report.sets) == set(GATE_NAMES)
    for counters in report.sets.values():
        assert REPORT_COUNTERS <= set(counters)
        assert counters["missing"] == counters["duplicate"] == counters["unexpected"] == 0
    assert report.sets["QS"]["expected"] == report.sets["QS"]["present"] == 3
    assert report.sets["NF"]["present"] == 2 and report.sets["CH"]["expected"] == 0


def test_same_count_substitution_is_a_mismatch_reported_without_the_ids(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path
) -> None:
    built = _release(instrument, tmp_path, runner_folder)
    substituted = [
        "qs-oracle-0001",
        "qs-oracle-0002",
        "qs-oracle-9999",
    ]  # same count, one id swapped
    built.write_visible_file(
        "data/optimization/QS/part0.jsonl", reference.gold_id_lines(substituted)
    )

    message = _mismatch(instrument, built)

    assert "qs-oracle-9999" not in message and "qs-oracle-0003" not in message


def test_duplicate_gold_id_in_a_data_file_is_a_mismatch(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path
) -> None:
    built = _release(instrument, tmp_path, runner_folder)
    built.write_visible_file(
        "data/optimization/NF/part0.jsonl",
        reference.gold_id_lines(["nf-oracle-0001", "nf-oracle-0001", "nf-oracle-0002"]),
    )

    message = _mismatch(instrument, built)

    assert "nf-oracle-0001" not in message


def test_unexpected_record_in_a_data_file_is_a_mismatch(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path
) -> None:
    built = _release(instrument, tmp_path, runner_folder)
    built.write_visible_file(
        "data/optimization/NF/part0.jsonl", reference.gold_id_lines([*NF_IDS, "nf-oracle-0003"])
    )

    message = _mismatch(instrument, built)

    assert "nf-oracle-0003" not in message


def test_seven_listed_records_without_a_data_file_are_reported_by_count(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path
) -> None:
    built = _release(instrument, tmp_path, runner_folder)
    manifest = built.manifest()
    manifest["records"]["LANG"]["optimization"] = SEVEN_MISSING
    built.write_manifest(manifest)

    message = _mismatch(instrument, built)

    assert not any(gold_id in message for gold_id in SEVEN_MISSING)
    assert re.search(r"\b7\b", message), message


@pytest.mark.parametrize(
    "line",
    [b'{"label": "no gold id here"}\n', b'{"gold_id": ""}\n', b'{"gold_id": null}\n'],
    ids=["missing_gold_id", "empty_gold_id", "null_gold_id"],
)
def test_a_line_without_a_usable_gold_id_is_a_mismatch(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path, line: bytes
) -> None:
    built = _release(instrument, tmp_path, runner_folder)
    built.write_visible_file(
        "data/optimization/QS/part0.jsonl", reference.gold_id_lines(QS_IDS) + line
    )

    _refused(instrument, built)


def test_other_fields_of_a_record_are_opaque_to_the_roster(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path
) -> None:
    built = _release(instrument, tmp_path, runner_folder)
    decorated = b"".join(
        (
            b'{"gold_id": "%s", "label": {"anything": [1, 2, 3]}, "split": "optimization"}\n'
            % gold_id.encode()
        )
        for gold_id in QS_IDS
    )
    built.write_visible_file("data/optimization/QS/part0.jsonl", decorated)

    report = _verify(instrument, built)

    assert report.ok is True and report.sets["QS"]["present"] == 3


def test_corpus_roster_by_digest_needs_no_data_file(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path
) -> None:
    built = _release(instrument, tmp_path, runner_folder)
    manifest = built.manifest()
    manifest["records"]["COV"] = {
        "by_digest": "a" * 64,
        "roster_counts": {"email_message": 6, "email_attachment": 5},
    }
    built.write_manifest(manifest)

    report = _verify(instrument, built)

    assert report.ok is True and report.sets["COV"]["missing"] == 0


def test_roster_mismatch_error_is_a_mem01_error(instrument: InstrumentLoader) -> None:
    exceptions = instrument("exceptions")

    assert issubclass(exceptions.RosterMismatchError, exceptions.Mem01Error)
