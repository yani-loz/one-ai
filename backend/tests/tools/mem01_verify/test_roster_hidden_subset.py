"""
Role: Seals fix-registry row A5 — `roster.verify_roster(release, *, split, hidden_root, sets)`
      on a hidden split with `sets` given opens and compares ONLY the named SETs' hidden files:
      with NF's hidden file absent, present but inconsistent, or present but unparseable, a
      QS-only verification
      succeeds, while the same call without `sets`, or naming NF, raises the roster mismatch
      that reports NF's missing records by count.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.roster, .lock, .exceptions (imported inside each test);
      tests.tools.mem01_verify.synthetic_release (proven by test_oracle_builders.py).
Key invariants:
  - The frozen synthetic release lists QS and NF records on the `test` split; the oracle then
    removes or corrupts NF's resolved hidden file (and its decoy), so NF is verifiable only if
    the roster never looks at it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.tools.mem01_verify import reference, synthetic_release
from tests.tools.mem01_verify.conftest import InstrumentLoader

QS_IDS = ["qs-hidden-0001", "qs-hidden-0002"]
NF_IDS = ["nf-hidden-0001", "nf-hidden-0002", "nf-hidden-0003"]
SPLIT = "test"


def _release(tmp_path: Path, runner_folder: Path) -> synthetic_release.SyntheticRelease:
    return synthetic_release.build_release(
        tmp_path,
        state="frozen",
        runner_sha256=reference.merkle_sha256_reference(runner_folder),
        hidden_records={"QS": QS_IDS, "NF": NF_IDS},
        hidden_split=SPLIT,
        optimization_records={"QS": ["qs-visible-0001"]},
    )


def _verify(
    instrument: InstrumentLoader, built: synthetic_release.SyntheticRelease, **options: object
) -> object:
    info = instrument("lock").verify_release_visible(built.path, expect_lock=None)
    return instrument("roster").verify_roster(
        info, split=SPLIT, hidden_root=built.hidden_root, **options
    )


def test_named_subset_verifies_qs_alone_while_the_full_roster_reports_the_absent_nf_by_count(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path
) -> None:
    exceptions = instrument("exceptions")
    built = _release(tmp_path, runner_folder)
    built.hidden_file(SPLIT, "NF").unlink()
    built.decoy_file(SPLIT, "NF").unlink()

    report = _verify(instrument, built, sets=["QS"])
    with pytest.raises(exceptions.RosterMismatchError) as without_sets:
        _verify(instrument, built)
    with pytest.raises(exceptions.RosterMismatchError):
        _verify(instrument, built, sets=["QS", "NF"])  # naming NF brings the mismatch back

    assert report.ok is True
    assert report.sets["QS"]["expected"] == report.sets["QS"]["present"] == len(QS_IDS)
    assert report.sets["QS"]["missing"] == 0
    assert without_sets.value.counts["NF"]["missing"] == len(NF_IDS)  # §16.14 `.counts`
    assert not any(gold_id in str(without_sets.value) for gold_id in NF_IDS)


def test_named_subset_never_compares_a_present_but_inconsistent_other_set(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path
) -> None:
    exceptions = instrument("exceptions")
    built = _release(tmp_path, runner_folder)
    built.hidden_file(SPLIT, "NF").write_bytes(reference.gold_id_lines(["nf-hidden-9999"]))

    report = _verify(instrument, built, sets=["QS"])
    with pytest.raises(exceptions.RosterMismatchError):
        _verify(instrument, built, sets=["NF"])  # positive control: NF alone is a mismatch

    assert report.ok is True and report.sets["QS"]["present"] == len(QS_IDS)


def test_named_subset_never_parses_a_present_but_unparseable_other_set(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path
) -> None:
    exceptions = instrument("exceptions")
    built = _release(tmp_path, runner_folder)
    built.hidden_file(SPLIT, "NF").write_bytes(b"\xff\xfe not json at all\n{\n")

    report = _verify(instrument, built, sets=["QS"])
    with pytest.raises(exceptions.RosterMismatchError):
        _verify(instrument, built, sets=["QS", "NF"])  # a non-JSON line is a mismatch (§16.14)
    with pytest.raises(exceptions.RosterMismatchError):
        _verify(instrument, built)

    assert report.ok is True and report.sets["QS"]["present"] == len(QS_IDS)
