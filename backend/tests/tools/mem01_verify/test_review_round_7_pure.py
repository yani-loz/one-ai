"""
Role: Seals fix-registry row A44 / contract §16.17(j) (amended) on the validation precondition — a
      `founder_reset` covers the one aborted attempt it names ONLY for that attempt's OWN
      candidate: the reset's `(code_hash, config_hash)` and the proposed run's pair must both
      equal the pair recorded on the aborted admission. `authorize A -> admit A1(A) -> abort A1
      -> reset A1 carrying B -> authorize B -> check(B)` is refused with a reason naming A1 (an
      abort can never be used to swap candidates under the same lock); the same sequence with
      the reset and the proposed run both carrying A is admitted; a reset carrying A never
      licenses a proposed run of B.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.validation_guard, .audit_file, .exceptions (imported inside each
      test through the `instrument` loader); tests.tools.mem01_verify.reference.
Key invariants:
  - The journal events the oracle authors carry exactly the §16.1 keys with `at` in the §16.1
    form, and every helper takes the candidate pair explicitly, so which pair sits on the
    admission, the reset and the proposed run is visible in each test.
  - The refusal is asserted to name the aborted attempt id; nothing is asserted about hashes.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import InstrumentLoader

LOCK = hashlib.sha256(b"oracle validation lock round 7").hexdigest()
CANDIDATE_A = (
    hashlib.sha256(b"candidate A code round 7").hexdigest(),
    hashlib.sha256(b"candidate A config round 7").hexdigest(),
)
CANDIDATE_B = (
    hashlib.sha256(b"candidate B code round 7").hexdigest(),
    hashlib.sha256(b"candidate B config round 7").hexdigest(),
)
FOUNDER = "founder"
SESSION = "oracle-session-round-7"


def _authorize(instrument: InstrumentLoader, path: Path, candidate: tuple[str, str]) -> None:
    code, config = candidate
    instrument("audit_file").append_event(
        path,
        {
            "type": "founder_authorization",
            "lock": LOCK,
            "code_hash": code,
            "config_hash": config,
            "principal": FOUNDER,
            "at": reference.EVENT_AT,
        },
    )


def _admit(instrument: InstrumentLoader, path: Path, candidate: tuple[str, str], index: int) -> str:
    code, config = candidate
    return instrument("validation_guard").record_admission(
        path,
        lock_sha256=LOCK,
        code_hash=code,
        config_hash=config,
        principal=FOUNDER,
        session=SESSION,
        run_id=reference.oracle_run_id(index),
    )


def _reset(
    instrument: InstrumentLoader, path: Path, attempt_id: str, candidate: tuple[str, str]
) -> None:
    code, config = candidate
    instrument("audit_file").append_event(
        path,
        {
            "type": "founder_reset",
            "attempt_id": attempt_id,
            "code_hash": code,
            "config_hash": config,
            "principal": FOUNDER,
            "reason": "aborted by a crash before any verdict",
            "at": reference.EVENT_AT,
        },
    )


def _check(instrument: InstrumentLoader, path: Path, candidate: tuple[str, str]) -> None:
    code, config = candidate
    instrument("validation_guard").check_validation_preconditions(
        path, lock_sha256=LOCK, code_hash=code, config_hash=config, principal=FOUNDER
    )


def _aborted_attempt_of_candidate_a(instrument: InstrumentLoader, audit: Path) -> str:
    """authorize A -> admit A1 with candidate A -> abort A1; return A1's attempt id."""
    _authorize(instrument, audit, CANDIDATE_A)
    first = _admit(instrument, audit, CANDIDATE_A, 1)
    instrument("validation_guard").record_abort(audit, first, "interrupted before the verdict")
    return first


def test_a_reset_carrying_another_candidate_never_licenses_that_candidate(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    exceptions = instrument("exceptions")
    audit = tmp_path / "audit.jsonl"
    first = _aborted_attempt_of_candidate_a(instrument, audit)
    _reset(instrument, audit, first, CANDIDATE_B)  # names A1, carries B's pair
    _authorize(instrument, audit, CANDIDATE_B)

    with pytest.raises(exceptions.ValidationRefusedError) as refused:
        _check(instrument, audit, CANDIDATE_B)

    assert first in str(refused.value)


def test_a_reset_carrying_the_aborted_attempts_own_candidate_admits_it_again(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    audit = tmp_path / "audit.jsonl"
    first = _aborted_attempt_of_candidate_a(instrument, audit)
    _reset(instrument, audit, first, CANDIDATE_A)  # names A1, carries A1's own pair

    _check(instrument, audit, CANDIDATE_A)  # positive control: admitted, no exception
    second = _admit(instrument, audit, CANDIDATE_A, 2)

    assert second != first


def test_a_reset_carrying_the_own_candidate_never_licenses_another_candidate(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    exceptions = instrument("exceptions")
    audit = tmp_path / "audit.jsonl"
    first = _aborted_attempt_of_candidate_a(instrument, audit)
    _reset(instrument, audit, first, CANDIDATE_A)
    _authorize(instrument, audit, CANDIDATE_B)

    with pytest.raises(exceptions.ValidationRefusedError) as refused:
        _check(instrument, audit, CANDIDATE_B)  # the proposed run is B: refused

    assert first in str(refused.value)
