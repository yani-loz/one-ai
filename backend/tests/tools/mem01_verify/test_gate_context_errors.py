"""
Role: Seals fix-registry row A11 — `gates.context.criterion_status(criterion, *, numerator,
      denominator, errors=0)` returns `ERROR` whenever `errors > 0`, before any other rule (R2),
      for a passing ratio, a failing ratio and a count invariant alike; with `errors=0` (or
      omitted) the ratio, minimum, zero-denominator and count rules decide as before.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.gates.context, .criteria (imported inside each test); the
      criteria annex through the instrument's loader (data the oracle may read, never edit).
Key invariants:
  - The criteria are real annex records chosen by id; the numerators and denominators are
    hand-picked around each criterion's own threshold and minimum (R12: nothing is measured).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.tools.mem01_verify.conftest import InstrumentLoader

REPLAY = "snap.replay_hash_equality"  # ratio, == 0, minimum 1000
NOISE = "nf.noise_stopped"  # ratio, >= 0.90, minimum 100
MERGES = "ident.provisional.c_no_unconfirmed_merge"  # count, == 0, no denominator


def _criterion(instrument: InstrumentLoader, criteria_path: Path, criterion_id: str) -> object:
    loaded = instrument("criteria").load_criteria(criteria_path)
    return next(criterion for criterion in loaded.criteria if criterion.id == criterion_id)


def test_errors_above_zero_turn_a_passing_ratio_into_error_before_any_other_rule(
    instrument: InstrumentLoader, criteria_path: Path
) -> None:
    status = instrument("gates.context").criterion_status
    replay = _criterion(instrument, criteria_path, REPLAY)
    noise = _criterion(instrument, criteria_path, NOISE)

    assert status(replay, numerator=0, denominator=1000) == "PASS"
    assert status(replay, numerator=0, denominator=1000, errors=0) == "PASS"
    assert status(replay, numerator=0, denominator=1000, errors=1) == "ERROR"
    assert status(replay, numerator=0, denominator=1000, errors=2) == "ERROR"
    assert status(noise, numerator=92, denominator=100) == "PASS"
    assert status(noise, numerator=89, denominator=100) == "FAIL"
    assert status(noise, numerator=92, denominator=100, errors=1) == "ERROR"
    assert status(noise, numerator=89, denominator=100, errors=1) == "ERROR"


def test_errors_zero_leaves_the_count_minimum_and_zero_denominator_rules_unchanged(
    instrument: InstrumentLoader, criteria_path: Path
) -> None:
    status = instrument("gates.context").criterion_status
    replay = _criterion(instrument, criteria_path, REPLAY)
    merges = _criterion(instrument, criteria_path, MERGES)

    assert status(merges, numerator=0, denominator=None) == "PASS"
    assert status(merges, numerator=1, denominator=None) == "FAIL"
    assert status(merges, numerator=0, denominator=None, errors=1) == "ERROR"
    assert status(replay, numerator=0, denominator=0, errors=0) == "ERROR"  # zero denominator
    assert status(replay, numerator=0, denominator=999, errors=0) == "ERROR"  # below minimum


def test_errors_is_accepted_by_keyword_only(
    instrument: InstrumentLoader, criteria_path: Path
) -> None:
    status = instrument("gates.context").criterion_status
    replay = _criterion(instrument, criteria_path, REPLAY)

    assert status(replay, numerator=0, denominator=1000, errors=1) == "ERROR"  # the keyword form
    with pytest.raises(TypeError):
        status(replay, 1, numerator=0, denominator=1000)  # `errors` is never positional
