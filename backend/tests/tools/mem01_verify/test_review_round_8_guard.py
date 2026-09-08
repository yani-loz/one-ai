"""
Role: Seals fix-registry row A47 / contract §16.18(b) on the validation precondition — an
      `attempt_id` that carries MORE THAN ONE `validation_admission` on the same lock is a
      malformed journal: `check_validation_preconditions` refuses with a reason naming that
      attempt id and nothing else (R5), whatever the candidates or principals, so
      `admit X(A) -> abort X -> founder_reset X(B) -> admit X(B)` (a second admission event under
      the same attempt id, distinct envelope id) can no longer license the candidate swap that
      §16.17(j) forbids; a duplicate carrying the SAME candidate is refused too. Precedence: an
      UNAUTHORIZED proposed candidate is refused by the (a) authorization rule FIRST — the
      generic refusal, an `unauthorized_attempt` event appended — even on a duplicated journal.
      The (j) sequences sealed by round 7 are unchanged, and `lock_state` of a duplicated
      journal without a verdict is still `attempted` (a precondition, not a state); the
      derivation clause the amendment makes explicit (every admission of a verdict-bearing
      attempt counts for `revoked`) is sealed by `test_review_round_8_guard_b.py`.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.validation_guard, .audit_file, .exceptions (imported inside each
      test through the `instrument` loader); tests.tools.mem01_verify.reference.
Key invariants:
  - The duplicate admission is appended with the contract's literal type `validation_admission`
    and exactly the keys `record_admission` writes (`attempt_id`, `lock`, `code_hash`,
    `config_hash`, `principal`, `session`, `run_id`) and no preset `event_id` — the envelope
    writer mints a distinct one — so the journal is well-formed line by line and only the
    repeated attempt id makes it malformed.
  - A refusal that "names the attempt id only" is checked against EVERY other fixture value:
    both candidates' hashes and the lock (full and 12-character prefixes), the principal (a
    distinctive string, never the bare word `founder`, which a legitimate refusal may contain
    inside `founder_reset`), the intruder string of the sibling seal, the session and both run
    ids.
  - Nothing here touches a hidden root: the precondition takes an audit path only, so "refused
    before the holdout is read" holds by construction of the API (no open-event assertion).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import InstrumentLoader

LOCK = hashlib.sha256(b"oracle validation lock round 8").hexdigest()
CANDIDATE_A = (
    hashlib.sha256(b"candidate A code round 8").hexdigest(),
    hashlib.sha256(b"candidate A config round 8").hexdigest(),
)
CANDIDATE_B = (
    hashlib.sha256(b"candidate B code round 8").hexdigest(),
    hashlib.sha256(b"candidate B config round 8").hexdigest(),
)
PRINCIPAL = "oracle-principal-round-8"
INTRUDER = "oracle-intruder-round-8"
SESSION = "oracle-session-round-8"
RUN_IDS = (reference.oracle_run_id(1), reference.oracle_run_id(2))


def _authorize(instrument: InstrumentLoader, path: Path, candidate: tuple[str, str]) -> None:
    code, config = candidate
    instrument("audit_file").append_event(
        path,
        {
            "type": "founder_authorization",
            "lock": LOCK,
            "code_hash": code,
            "config_hash": config,
            "principal": PRINCIPAL,
            "at": reference.EVENT_AT,
        },
    )


def _admit(instrument: InstrumentLoader, path: Path, candidate: tuple[str, str]) -> str:
    code, config = candidate
    return instrument("validation_guard").record_admission(
        path,
        lock_sha256=LOCK,
        code_hash=code,
        config_hash=config,
        principal=PRINCIPAL,
        session=SESSION,
        run_id=RUN_IDS[0],
    )


def _duplicate_admission(
    instrument: InstrumentLoader, path: Path, attempt_id: str, candidate: tuple[str, str]
) -> None:
    """A second `validation_admission` under an existing attempt id, keys as the guard writes."""
    code, config = candidate
    instrument("audit_file").append_event(
        path,
        {
            "type": "validation_admission",
            "attempt_id": attempt_id,
            "lock": LOCK,
            "code_hash": code,
            "config_hash": config,
            "principal": PRINCIPAL,
            "session": SESSION,
            "run_id": RUN_IDS[1],
        },
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
            "principal": PRINCIPAL,
            "reason": "aborted by a crash before any verdict",
            "at": reference.EVENT_AT,
        },
    )


def _check(instrument: InstrumentLoader, path: Path, candidate: tuple[str, str]) -> None:
    code, config = candidate
    instrument("validation_guard").check_validation_preconditions(
        path, lock_sha256=LOCK, code_hash=code, config_hash=config, principal=PRINCIPAL
    )


def _refusal(instrument: InstrumentLoader, path: Path, candidate: tuple[str, str]) -> str:
    with pytest.raises(instrument("exceptions").ValidationRefusedError) as refused:
        _check(instrument, path, candidate)
    return str(refused.value)


def _events_of(path: Path, event_type: str) -> list[dict]:
    return [event for event in reference.read_jsonl(path) if event.get("type") == event_type]


def _aborted_attempt(instrument: InstrumentLoader, audit: Path) -> str:
    """authorize A -> admit X with candidate A -> abort X; return X."""
    _authorize(instrument, audit, CANDIDATE_A)
    attempt_id = _admit(instrument, audit, CANDIDATE_A)
    instrument("validation_guard").record_abort(audit, attempt_id, "interrupted before the verdict")
    return attempt_id


def _duplicated_swap_journal(instrument: InstrumentLoader, audit: Path) -> str:
    """admit X(A) -> abort X -> founder_reset X(B) -> a second admission X(B); return X."""
    attempt_id = _aborted_attempt(instrument, audit)
    _reset(instrument, audit, attempt_id, CANDIDATE_B)  # names X, carries B's pair
    _duplicate_admission(instrument, audit, attempt_id, CANDIDATE_B)  # X again, now with B
    return attempt_id


def _assert_names_no_secret(message: str) -> None:
    """R5: no hash (not even a 12-character prefix), principal, intruder, session or run id."""
    hashes = (*CANDIDATE_A, *CANDIDATE_B, LOCK)
    prefixes = tuple(digest[:12] for digest in hashes)
    for secret in (*hashes, *prefixes, PRINCIPAL, INTRUDER, SESSION, *RUN_IDS):
        assert secret not in message


def _assert_names_the_attempt_only(message: str, attempt_id: str) -> None:
    assert attempt_id in message
    _assert_names_no_secret(message)


# ── the seal: a duplicate admission is a malformed journal ──────────────────────────────


def test_a_duplicate_admission_carrying_another_candidate_is_refused_naming_the_attempt_only(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    audit = tmp_path / "audit.jsonl"
    attempt_id = _duplicated_swap_journal(instrument, audit)
    _authorize(instrument, audit, CANDIDATE_B)

    message = _refusal(instrument, audit, CANDIDATE_B)

    _assert_names_the_attempt_only(message, attempt_id)


def test_a_duplicate_admission_with_the_same_candidate_is_refused_as_malformed(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    audit = tmp_path / "audit.jsonl"
    attempt_id = _aborted_attempt(instrument, audit)
    _reset(instrument, audit, attempt_id, CANDIDATE_A)  # a covering reset for X's own candidate
    _duplicate_admission(instrument, audit, attempt_id, CANDIDATE_A)  # X again, same candidate

    message = _refusal(instrument, audit, CANDIDATE_A)  # any duplicate is malformed

    _assert_names_the_attempt_only(message, attempt_id)


# ── controls: precedence, the (j) sequences and the lock state are unchanged ────────────


def test_an_unauthorized_candidate_on_a_duplicated_journal_is_refused_by_the_authorization_rule(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    audit = tmp_path / "audit.jsonl"
    attempt_id = _duplicated_swap_journal(instrument, audit)  # B is NOT authorized here
    unauthorized_before = len(_events_of(audit, "unauthorized_attempt"))

    message = _refusal(instrument, audit, CANDIDATE_B)

    assert len(_events_of(audit, "unauthorized_attempt")) == unauthorized_before + 1  # rule (a)
    assert attempt_id not in message  # §16.18(b): the authorization refusal names no attempt
    _assert_names_no_secret(message)


def test_a_covering_reset_for_the_own_candidate_still_admits_that_candidate(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    audit = tmp_path / "audit.jsonl"
    attempt_id = _aborted_attempt(instrument, audit)
    _reset(instrument, audit, attempt_id, CANDIDATE_A)

    _check(instrument, audit, CANDIDATE_A)  # positive control: admitted, no exception
    second = _admit(instrument, audit, CANDIDATE_A)

    assert second != attempt_id


def test_a_covering_reset_for_the_own_candidate_still_refuses_another_candidate(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    audit = tmp_path / "audit.jsonl"
    attempt_id = _aborted_attempt(instrument, audit)
    _reset(instrument, audit, attempt_id, CANDIDATE_A)
    _authorize(instrument, audit, CANDIDATE_B)

    message = _refusal(instrument, audit, CANDIDATE_B)

    _assert_names_the_attempt_only(message, attempt_id)


def test_lock_state_with_a_duplicate_admission_is_still_attempted(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    guard = instrument("validation_guard")
    audit = tmp_path / "audit.jsonl"
    _duplicated_swap_journal(instrument, audit)
    _authorize(instrument, audit, CANDIDATE_B)

    state = guard.lock_state(audit, LOCK)

    assert state == "attempted"  # a precondition refuses; the state machine is untouched
