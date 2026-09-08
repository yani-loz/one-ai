"""
Role: Seals the event-sourced validation journal of contract §3.7 / §1.3 `validation_guard` /
      §16.1-16.2 on a temporary audit file — preconditions (authorization for exactly the
      candidate pair; lock state open, or attempted with one abort covered by a founder reset),
      admission before execution with exactly the literal event keys, verdict reservation
      treated as printed, a printed verdict or a second abort consuming the lock forever,
      revocation, and the derived `lock_state`.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.validation_guard, .audit_file, .exceptions (imported inside each
      test); tests.tools.mem01_verify.reference (id and stamp forms).
Key invariants:
  - Events the oracle authors (`founder_authorization`, `founder_reset`, `lock_revoked`,
    `holdout_consumed`) carry exactly the §16.1 keys with `at` in the §16.1 form.
  - The journal only ever grows: every step asserts the previous bytes are a prefix.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import InstrumentLoader

LOCK = hashlib.sha256(b"oracle validation lock").hexdigest()
CODE = hashlib.sha256(b"candidate code").hexdigest()
CONFIG = hashlib.sha256(b"candidate config").hexdigest()
OTHER_CODE = hashlib.sha256(b"other code").hexdigest()
FOUNDER = "founder"
SESSION = "oracle-session"
BASE_KEYS = {"type", "event_id", "at"}
VERDICT_LINE = (
    "STEP1 ACCEPTANCE: 17/17 PASS | provisional=0:- | validation=complete | directional=- | "
    f"run_id={reference.oracle_run_id(1)} | lock=sha256:{LOCK} | runner=sha256:{'0' * 64}"
)
ADMISSION_KEYS = BASE_KEYS | {
    "attempt_id",
    "lock",
    "code_hash",
    "config_hash",
    "principal",
    "session",
    "run_id",
}


def _authorize(
    instrument: InstrumentLoader,
    path: Path,
    *,
    lock: str = LOCK,
    code: str = CODE,
    config: str = CONFIG,
) -> None:
    instrument("audit_file").append_event(
        path,
        {
            "type": "founder_authorization",
            "lock": lock,
            "code_hash": code,
            "config_hash": config,
            "principal": FOUNDER,
            "at": reference.EVENT_AT,
        },
    )


def _reset(instrument: InstrumentLoader, path: Path, attempt_id: str, *, code: str = CODE) -> None:
    instrument("audit_file").append_event(
        path,
        {
            "type": "founder_reset",
            "attempt_id": attempt_id,
            "code_hash": code,
            "config_hash": CONFIG,
            "principal": FOUNDER,
            "reason": "aborted by a crash before any verdict",
            "at": reference.EVENT_AT,
        },
    )


def _check(
    instrument: InstrumentLoader,
    path: Path,
    *,
    code: str = CODE,
    config: str = CONFIG,
    principal: str = FOUNDER,
) -> None:
    instrument("validation_guard").check_validation_preconditions(
        path, lock_sha256=LOCK, code_hash=code, config_hash=config, principal=principal
    )


def _admit(
    instrument: InstrumentLoader, path: Path, *, principal: str = FOUNDER, index: int = 1
) -> str:
    return instrument("validation_guard").record_admission(
        path,
        lock_sha256=LOCK,
        code_hash=CODE,
        config_hash=CONFIG,
        principal=principal,
        session=SESSION,
        run_id=reference.oracle_run_id(index),
    )


def _events(instrument: InstrumentLoader, path: Path, event_type: str) -> list[dict]:
    events = instrument("audit_file").read_events(path)
    return [event for event in events if event["type"] == event_type]


def _assert_stamped(event: dict) -> None:
    assert re.fullmatch(reference.UUID4_PATTERN, event["event_id"]), event["event_id"]
    assert re.fullmatch(reference.EVENT_AT_PATTERN, event["at"]), event["at"]


def test_lock_starts_open_and_is_refused_without_an_authorization(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    guard = instrument("validation_guard")
    exceptions = instrument("exceptions")
    audit = tmp_path / "audit.jsonl"

    assert guard.lock_state(audit, LOCK) == "open"
    with pytest.raises(exceptions.ValidationRefusedError):
        _check(instrument, audit)
    _authorize(instrument, audit)
    _check(instrument, audit)  # positive control: passes once authorized


def test_authorization_must_name_exactly_the_candidate_pair_and_lock(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    exceptions = instrument("exceptions")
    wrong_pair, wrong_lock = tmp_path / "pair.jsonl", tmp_path / "lock.jsonl"
    _authorize(instrument, wrong_pair, code=OTHER_CODE)
    _authorize(instrument, wrong_lock, lock=hashlib.sha256(b"another lock").hexdigest())

    with pytest.raises(exceptions.ValidationRefusedError):
        _check(instrument, wrong_pair)
    with pytest.raises(exceptions.ValidationRefusedError):
        _check(instrument, wrong_lock)


def test_admission_is_appended_before_execution_with_the_literal_keys_and_only_grows_the_journal(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    audit = tmp_path / "audit.jsonl"
    _authorize(instrument, audit)
    before = audit.read_bytes()

    attempt_id = _admit(instrument, audit, index=4)

    after = audit.read_bytes()
    assert isinstance(attempt_id, str) and attempt_id
    assert after.startswith(before) and len(after) > len(before)
    admissions = _events(instrument, audit, "validation_admission")
    assert len(admissions) == 1 and set(admissions[0]) == ADMISSION_KEYS
    _assert_stamped(admissions[0])
    assert admissions[0]["attempt_id"] == attempt_id
    assert admissions[0]["lock"] == LOCK and admissions[0]["principal"] == FOUNDER
    assert (admissions[0]["code_hash"], admissions[0]["config_hash"]) == (CODE, CONFIG)
    assert admissions[0]["session"] == SESSION
    assert admissions[0]["run_id"] == reference.oracle_run_id(4)


def test_abort_reserve_and_printed_events_carry_the_literal_keys(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    guard = instrument("validation_guard")
    aborted, printed = tmp_path / "aborted.jsonl", tmp_path / "printed.jsonl"
    _authorize(instrument, aborted)
    _authorize(instrument, printed)
    first = _admit(instrument, aborted)
    second = _admit(instrument, printed)

    guard.record_abort(aborted, first, "interrupted before the verdict")
    guard.reserve_verdict(printed, second)
    guard.record_verdict_printed(printed, second, VERDICT_LINE)

    (abort,) = _events(instrument, aborted, "validation_abort")
    assert set(abort) == BASE_KEYS | {"attempt_id", "reason"}
    assert abort["attempt_id"] == first and abort["reason"] == "interrupted before the verdict"
    (reserved,) = _events(instrument, printed, "validation_verdict_reserved")
    assert set(reserved) == BASE_KEYS | {"attempt_id"} and reserved["attempt_id"] == second
    (done,) = _events(instrument, printed, "validation_verdict_printed")
    assert set(done) == BASE_KEYS | {"attempt_id", "verdict_sha256"}
    assert done["attempt_id"] == second
    assert done["verdict_sha256"] == hashlib.sha256(VERDICT_LINE.encode("utf-8")).hexdigest()
    for event in (abort, reserved, done):
        _assert_stamped(event)


def test_one_abort_covered_by_a_reset_reopens_the_attempt_once(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    guard = instrument("validation_guard")
    exceptions = instrument("exceptions")
    audit = tmp_path / "audit.jsonl"
    _authorize(instrument, audit)
    first = _admit(instrument, audit)
    guard.record_abort(audit, first, "interrupted before the verdict")

    with pytest.raises(exceptions.ValidationRefusedError):
        _check(instrument, audit)  # aborted, not yet reset
    _reset(instrument, audit, first)
    assert guard.lock_state(audit, LOCK) == "attempted"
    _check(instrument, audit)  # positive control: one covered abort is admissible

    second = _admit(instrument, audit, index=2)
    guard.record_abort(audit, second, "interrupted again")
    _reset(instrument, audit, second)
    assert guard.lock_state(audit, LOCK) == "consumed"
    with pytest.raises(exceptions.ValidationRefusedError):
        _check(instrument, audit)


def test_a_reset_for_another_candidate_does_not_cover_the_abort(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    guard = instrument("validation_guard")
    exceptions = instrument("exceptions")
    audit = tmp_path / "audit.jsonl"
    _authorize(instrument, audit)
    first = _admit(instrument, audit)
    guard.record_abort(audit, first, "interrupted")

    _reset(instrument, audit, first, code=OTHER_CODE)

    with pytest.raises(exceptions.ValidationRefusedError):
        _check(instrument, audit)
    _reset(instrument, audit, first)  # positive control: the same candidate's reset covers it
    _check(instrument, audit)


def test_a_printed_verdict_consumes_the_lock_forever(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    guard = instrument("validation_guard")
    exceptions = instrument("exceptions")
    audit = tmp_path / "audit.jsonl"
    _authorize(instrument, audit)
    attempt_id = _admit(instrument, audit)

    guard.reserve_verdict(audit, attempt_id)
    guard.record_verdict_printed(audit, attempt_id, VERDICT_LINE)

    assert guard.lock_state(audit, LOCK) == "consumed"
    with pytest.raises(exceptions.ValidationRefusedError):
        _check(instrument, audit)
    _reset(instrument, audit, attempt_id)  # a reset cannot revive a printed verdict
    with pytest.raises(exceptions.ValidationRefusedError):
        _check(instrument, audit)


def test_a_reserved_verdict_without_the_printed_record_counts_as_printed(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    guard = instrument("validation_guard")
    exceptions = instrument("exceptions")
    audit = tmp_path / "audit.jsonl"
    _authorize(instrument, audit)
    attempt_id = _admit(instrument, audit)

    guard.reserve_verdict(audit, attempt_id)

    assert guard.lock_state(audit, LOCK) == "consumed"
    _reset(instrument, audit, attempt_id)
    with pytest.raises(exceptions.ValidationRefusedError):
        _check(instrument, audit)


def test_lock_revoked_event_and_foreign_principal_verdict_revoke_the_lock(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    guard = instrument("validation_guard")
    audit_file = instrument("audit_file")
    exceptions = instrument("exceptions")
    revoked, foreign = tmp_path / "revoked.jsonl", tmp_path / "foreign.jsonl"
    _authorize(instrument, revoked)
    audit_file.append_event(
        revoked,
        {
            "type": "lock_revoked",
            "lock": LOCK,
            "cause": "manual",
            "principal": FOUNDER,
            "at": reference.EVENT_AT,
        },
    )
    _authorize(instrument, foreign)
    attempt_id = _admit(instrument, foreign, principal="intruder")
    guard.reserve_verdict(foreign, attempt_id)
    guard.record_verdict_printed(foreign, attempt_id, VERDICT_LINE)

    assert guard.lock_state(revoked, LOCK) == "revoked"
    assert guard.lock_state(foreign, LOCK) == "revoked"
    with pytest.raises(exceptions.ValidationRefusedError):
        _check(instrument, revoked)
    with pytest.raises(exceptions.ValidationRefusedError):
        _check(instrument, foreign)


def test_holdout_consumed_event_closes_the_lock(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    guard = instrument("validation_guard")
    audit_file = instrument("audit_file")
    exceptions = instrument("exceptions")
    audit = tmp_path / "audit.jsonl"
    _authorize(instrument, audit)
    _check(instrument, audit)  # positive control
    audit_file.append_event(
        audit,
        {"type": "holdout_consumed", "lock": LOCK, "cause": "manual", "at": reference.EVENT_AT},
    )

    assert guard.lock_state(audit, LOCK) == "consumed"
    with pytest.raises(exceptions.ValidationRefusedError):
        _check(instrument, audit)


def test_validation_refused_error_is_a_mem01_error(instrument: InstrumentLoader) -> None:
    exceptions = instrument("exceptions")

    assert issubclass(exceptions.ValidationRefusedError, exceptions.Mem01Error)
