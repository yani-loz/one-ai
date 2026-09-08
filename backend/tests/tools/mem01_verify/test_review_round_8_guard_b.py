"""
Role: Seals the derivation clause of fix-registry row A47 / contract §16.18(b) on `lock_state` —
      the `foreign` test behind `revoked` considers EVERY `validation_admission` event of a
      verdict-bearing attempt, not the last one and not the first: with a verdict printed under
      attempt X, an admission by a principal outside the lock's authorizations ANYWHERE among
      X's admissions (first, middle or last) makes the lock `revoked`, while two admissions that
      are both authorized make it `consumed` (duplicates alone never revoke — even by two
      DIFFERENT authorized principals). The precondition
      half of A47 is sealed by `test_review_round_8_guard.py`.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.validation_guard, .audit_file (imported inside each test through
      the `instrument` loader); tests.tools.mem01_verify.reference.
Key invariants:
  - Every journal is one verdict-bearing attempt built the way the sealed `revoked` transition
    is built today (`record_admission`, `reserve_verdict`, `record_verdict_printed`); the extra
    admissions are appended with the contract's literal type `validation_admission` and exactly
    the keys `record_admission` writes, the envelope writer minting each distinct `event_id`.
  - The intruder and the authorized principal are distinctive strings; only the ORDER of the
    admissions varies between the cases, so the derivation's choice of admission is what each
    case decides.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import InstrumentLoader

LOCK = hashlib.sha256(b"oracle validation lock round 8b").hexdigest()
CANDIDATE = (
    hashlib.sha256(b"candidate code round 8b").hexdigest(),
    hashlib.sha256(b"candidate config round 8b").hexdigest(),
)
PRINCIPAL = "oracle-principal-round-8"
PRINCIPAL_TWO = "oracle-principal-two-round-8"
INTRUDER = "oracle-intruder-round-8"
SESSION = "oracle-session-round-8"
VERDICT_LINE = "STEP1 PASS oracle-round-8"


def _authorize(instrument: InstrumentLoader, path: Path, *, principal: str = PRINCIPAL) -> None:
    code, config = CANDIDATE
    instrument("audit_file").append_event(
        path,
        {
            "type": "founder_authorization",
            "lock": LOCK,
            "code_hash": code,
            "config_hash": config,
            "principal": principal,
            "at": reference.EVENT_AT,
        },
    )


def _admit(instrument: InstrumentLoader, path: Path, *, principal: str) -> str:
    code, config = CANDIDATE
    return instrument("validation_guard").record_admission(
        path,
        lock_sha256=LOCK,
        code_hash=code,
        config_hash=config,
        principal=principal,
        session=SESSION,
        run_id=reference.oracle_run_id(1),
    )


def _admit_again(
    instrument: InstrumentLoader, path: Path, attempt_id: str, *, principal: str, index: int
) -> None:
    """Another `validation_admission` under the SAME attempt id, keys as the guard writes."""
    code, config = CANDIDATE
    instrument("audit_file").append_event(
        path,
        {
            "type": "validation_admission",
            "attempt_id": attempt_id,
            "lock": LOCK,
            "code_hash": code,
            "config_hash": config,
            "principal": principal,
            "session": SESSION,
            "run_id": reference.oracle_run_id(index),
        },
    )


def _verdict(instrument: InstrumentLoader, path: Path, attempt_id: str) -> None:
    """X reserves and prints a verdict, exactly as the sealed `revoked` transition is built."""
    guard = instrument("validation_guard")
    guard.reserve_verdict(path, attempt_id)
    guard.record_verdict_printed(path, attempt_id, VERDICT_LINE)


def _state_after(
    instrument: InstrumentLoader,
    tmp_path: Path,
    principals: tuple[str, ...],
    *,
    authorized: tuple[str, ...] = (PRINCIPAL,),
) -> str:
    """authorize -> admit X by `principals[0]` -> re-admit X by the rest -> verdict -> state."""
    audit = tmp_path / "audit.jsonl"
    for principal in authorized:
        _authorize(instrument, audit, principal=principal)
    attempt_id = _admit(instrument, audit, principal=principals[0])
    for index, principal in enumerate(principals[1:], start=2):
        _admit_again(instrument, audit, attempt_id, principal=principal, index=index)
    _verdict(instrument, audit, attempt_id)
    return instrument("validation_guard").lock_state(audit, LOCK)


# ── the seal: a foreign admission anywhere among X's admissions revokes ─────────────────


def test_an_authorized_admission_appended_after_a_foreign_one_cannot_turn_revoked_into_consumed(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    state = _state_after(instrument, tmp_path, (INTRUDER, PRINCIPAL))

    assert state == "revoked", state  # last-wins reads the authorized one: wrong


def test_a_foreign_admission_between_two_authorized_ones_still_revokes_the_lock(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    state = _state_after(instrument, tmp_path, (PRINCIPAL, INTRUDER, PRINCIPAL))

    assert state == "revoked", state  # neither first-wins nor last-wins sees the intruder


# ── controls: green today, and they must stay green after the fix ────────────────────────


def test_a_foreign_admission_as_the_last_one_revokes_the_lock(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    state = _state_after(instrument, tmp_path, (PRINCIPAL, INTRUDER))

    assert state == "revoked"


def test_a_foreign_principal_verdict_alone_revokes_the_lock(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    state = _state_after(instrument, tmp_path, (INTRUDER,))

    assert state == "revoked"  # the sealed transition as built today


def test_two_authorized_admissions_with_a_verdict_are_consumed_never_revoked(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    state = _state_after(instrument, tmp_path, (PRINCIPAL, PRINCIPAL))

    assert state == "consumed"  # duplicates alone never revoke


def test_two_different_authorized_principals_with_a_verdict_are_consumed_never_revoked(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    state = _state_after(
        instrument, tmp_path, (PRINCIPAL, PRINCIPAL_TWO), authorized=(PRINCIPAL, PRINCIPAL_TWO)
    )

    assert state == "consumed"  # disagreeing principals alone never revoke: both are authorized
