"""
Role: The event-sourced validation journal of contract §3.7 — the guard that decides whether the
      founder's one `--validation` run may touch the holdout. It derives `lock_state` from the
      release's `audit.jsonl`, refuses a run whose authorization is missing or whose lock is
      already attempted-without-a-reset, consumed or revoked, appends the admission,
      verdict-reservation, verdict-printed and abort events of §16.1, and seals an
      admitted-then-aborted attempt's report directory as `<run_id>.sealed` (§16.16h).
Used by: the runner `tools.mem01_verify.verify_step1` (the step-6 admission and the verdict
      sequence); sealed by `backend/tests/tools/mem01_verify/test_validation_guard.py`.
Depends on: `tools.mem01_verify.audit_file` (the §16.12 envelope writer / reader) and
      `.exceptions` (`ValidationRefusedError`, `ValidationGuardError`).
Key invariants:
  - Nothing is ever updated in place: the journal only grows, and every decision is a derivation
    over the events read back from it.
  - The events this module writes carry EXACTLY `type`, `event_id`, `at` plus the keys §16.1
    lists; `event_id` and `at` are stamped by the envelope writer, never by a caller.
  - A verdict RESERVATION counts as a printed verdict (§3.7): a crash after printing can never
    masquerade as an unprinted verdict, so the holdout stays consumed.
  - State precedence is `revoked` > `consumed` > `attempted` > `open`; a printed verdict under a
    principal the founder did not authorize revokes the lock rather than consuming it. That
    `foreign` test reads EVERY `validation_admission` recorded under a verdict-bearing attempt,
    not the last one (§16.18(b)), so an authorized admission appended after a foreign one can
    never turn `revoked` back into `consumed`.
  - Sealing is a RENAME, never a copy: `seal_aborted_attempt` refuses an existing
    `<run_id>.sealed` target rather than overwriting an earlier attempt, so a re-run can
    never read (or silently replace) a sealed attempt's artifacts (§3.7).
  - `check_validation_preconditions` never refuses on the caller's principal — an unauthorized
    principal is admissible and its verdict revokes the lock, which is how §3.7 detects it. A
    call with NO authorization at all appends `unauthorized_attempt` and refuses.
  - For that precondition an admission carrying NO terminal event (`validation_abort`,
    `validation_verdict_reserved`, `validation_verdict_printed`) counts as an ABORT of its own
    attempt (§16.17(j)): a crash between the admission and its abort record is a crash, not a
    licence to run again. The covering `founder_reset` — and the proposed run — must carry the
    pair the aborted ADMISSION recorded, so an abort can never be used to swap candidates under
    one lock. A refusal names attempt ids alone — instrument identifiers, never personal data
    (R5). `lock_state` is a STATE and is unchanged by either rule.
  - An attempt id carrying MORE THAN ONE `validation_admission` on one lock is a malformed
    journal (§16.18(b)): the precondition refuses it before any candidate is resolved, so the
    projection's last-wins admission can never license the candidate swap (j) forbids. The
    refusal is ordered after the authorization check of §3.7 and before every check of the
    covered abort; `record_admission` and `lock_state` are unchanged.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from tools.mem01_verify import audit_file
from tools.mem01_verify.exceptions import ValidationGuardError, ValidationRefusedError

AUTHORIZATION_EVENT = "founder_authorization"
ADMISSION_EVENT = "validation_admission"
RESERVED_EVENT = "validation_verdict_reserved"
PRINTED_EVENT = "validation_verdict_printed"
ABORT_EVENT = "validation_abort"
RESET_EVENT = "founder_reset"
HOLDOUT_CONSUMED_EVENT = "holdout_consumed"
LOCK_REVOKED_EVENT = "lock_revoked"
UNAUTHORIZED_EVENT = "unauthorized_attempt"

#: The suffix §3.7 gives an aborted validation attempt's report directory once it is sealed.
SEALED_SUFFIX = ".sealed"

LockState = Literal["open", "attempted", "consumed", "revoked"]


@dataclass(frozen=True)
class _Journal:
    """The events of one release audit file that bear on ONE release lock.

    `admissions` maps attempt id → EVERY `validation_admission` recorded under that id on this
    lock, in ledger order: the state derivation reads all of them (§16.18(b)) while the
    candidate of an attempt is the LAST one it recorded. The abort, reset and verdict events
    carry no lock of their own and are attached to the lock through their attempt id.
    """

    authorized_principals: frozenset[str]
    candidates: tuple[tuple[str, str], ...]
    admissions: dict[str, tuple[dict, ...]]
    aborts: tuple[dict, ...]
    resets: tuple[dict, ...]
    verdict_attempts: tuple[str, ...]
    revoked: bool
    holdout_consumed: bool

    @property
    def duplicate_admissions(self) -> frozenset[str]:
        """The attempt ids carrying more than one admission on this lock (§16.18(b))."""
        return frozenset(
            attempt_id for attempt_id, events in self.admissions.items() if len(events) > 1
        )


def _load(audit_path: Path, lock_sha256: str) -> _Journal:
    """Read the audit file and project the events that bear on `lock_sha256`."""
    events = audit_file.read_events(audit_path)
    authorizations = [
        event
        for event in events
        if event.get("type") == AUTHORIZATION_EVENT and event.get("lock") == lock_sha256
    ]
    admissions = _admissions_by_attempt(events, lock_sha256)
    mine = set(admissions)
    verdict_attempts = [
        str(event.get("attempt_id"))
        for event in events
        if event.get("type") in (RESERVED_EVENT, PRINTED_EVENT)
        and str(event.get("attempt_id")) in mine
    ]
    return _Journal(
        authorized_principals=frozenset(str(event.get("principal")) for event in authorizations),
        candidates=tuple(
            (str(event.get("code_hash")), str(event.get("config_hash"))) for event in authorizations
        ),
        admissions=admissions,
        aborts=tuple(_of_type(events, ABORT_EVENT, mine)),
        resets=tuple(_of_type(events, RESET_EVENT, mine)),
        verdict_attempts=tuple(dict.fromkeys(verdict_attempts)),
        revoked=any(
            event.get("type") == LOCK_REVOKED_EVENT and event.get("lock") == lock_sha256
            for event in events
        ),
        holdout_consumed=any(
            event.get("type") == HOLDOUT_CONSUMED_EVENT and event.get("lock") == lock_sha256
            for event in events
        ),
    )


def _admissions_by_attempt(events: list[dict], lock_sha256: str) -> dict[str, tuple[dict, ...]]:
    """Group this lock's `validation_admission` events by attempt id, keeping ledger order.

    A well-formed journal records exactly one admission per attempt id; a longer tuple is the
    malformed journal §16.18(b) refuses, and every event in it counts for the `foreign` test.
    """
    grouped: dict[str, list[dict]] = {}
    for event in events:
        if event.get("type") != ADMISSION_EVENT or event.get("lock") != lock_sha256:
            continue
        grouped.setdefault(str(event.get("attempt_id")), []).append(event)
    return {attempt_id: tuple(admissions) for attempt_id, admissions in grouped.items()}


def _of_type(events: list[dict], event_type: str, attempt_ids: set[str]) -> list[dict]:
    """The events of one type whose attempt id belongs to this lock's admissions."""
    return [
        event
        for event in events
        if event.get("type") == event_type and str(event.get("attempt_id")) in attempt_ids
    ]


def _derive(journal: _Journal) -> LockState:
    """Derive the §3.7 lock state, precedence revoked > consumed > attempted > open.

    `foreign` reads EVERY admission of a verdict-bearing attempt (§16.18(b)): one unauthorized
    principal anywhere among them revokes, whatever was appended after it.
    """
    foreign = any(
        str(admission.get("principal")) not in journal.authorized_principals
        for attempt_id in journal.verdict_attempts
        for admission in journal.admissions[attempt_id]
    )
    if journal.revoked or foreign:
        return "revoked"
    if journal.holdout_consumed or journal.verdict_attempts or len(journal.aborts) >= 2:
        return "consumed"
    if journal.admissions:
        return "attempted"
    return "open"


def lock_state(audit_path: Path, lock_sha256: str) -> LockState:
    """Return the state of one release lock's holdout, derived from the journal (§3.7).

    Args:
        audit_path: `<release>/audit.jsonl`. A missing file reads as an empty journal.
        lock_sha256: The release lock the events are filtered by.

    Returns:
        `open` (no admission yet), `attempted` (an admission that neither consumed nor revoked
        the lock), `consumed` (a printed or reserved verdict, a second abort, or an explicit
        `holdout_consumed`), or `revoked` (a `lock_revoked` event, or a verdict under a
        principal the founder did not authorize).

    Raises:
        IntegrityViolationError: The journal's last line is torn.
    """
    return _derive(_load(audit_path, lock_sha256))


def check_validation_preconditions(
    audit_path: Path,
    *,
    lock_sha256: str,
    code_hash: str,
    config_hash: str,
    principal: str,
) -> None:
    """Refuse the `--validation` run unless §3.7's preconditions hold, BEFORE any hidden read.

    The run is admissible when a `founder_authorization` for this lock names exactly this
    `(code_hash, config_hash)` candidate AND the lock is `open`, or `attempted` with exactly one
    aborted attempt covered by a `founder_reset` for the SAME candidate — the pair the aborted
    ADMISSION itself recorded, which the reset and this call must both carry (§16.17(j) as
    amended). An aborted attempt is either an explicit `validation_abort` or an admission that
    carries no terminal event at all (§16.17(j)). A missing authorization also appends an
    `unauthorized_attempt` event naming `principal` (§3.7).

    An attempt id carrying more than one `validation_admission` on this lock is a malformed
    journal and refuses before any candidate is resolved (§16.18(b)); §3.7's own order puts that
    refusal after the authorization check — whose refusal names no attempt — and before every
    check of the covered abort.

    Raises:
        ValidationRefusedError: On any of the above — the holdout stays unread.
        IntegrityViolationError: The journal's last line is torn.
    """
    journal = _load(audit_path, lock_sha256)
    if (code_hash, config_hash) not in journal.candidates:
        audit_file.append_event(
            audit_path,
            {
                "type": UNAUTHORIZED_EVENT,
                "lock": lock_sha256,
                "code_hash": code_hash,
                "config_hash": config_hash,
                "principal": principal,
            },
        )
        raise ValidationRefusedError(
            "no founder_authorization for this lock names this candidate; refusing to read the "
            "holdout"
        )
    state = _derive(journal)
    if state == "open":
        return
    if state != "attempted":
        raise ValidationRefusedError(
            f"the holdout of this lock is {state}; no further validation run is admissible"
        )
    _refuse_duplicate_admissions(journal)
    _require_covered_abort(journal, code_hash, config_hash)


def _refuse_duplicate_admissions(journal: _Journal) -> None:
    """Refuse a lock whose journal records an attempt id twice (§16.18(b)).

    The refusal names the duplicated attempt ids and NOTHING else — no candidate, no principal,
    no lock (R5) — and is raised before `_require_covered_abort` resolves any candidate, so a
    second admission appended under an aborted attempt's id can never redefine what that attempt
    was admitted for.

    Raises:
        ValidationRefusedError: At least one attempt id carries more than one admission.
    """
    duplicated = journal.duplicate_admissions
    if not duplicated:
        return
    raise ValidationRefusedError(
        f"more than one validation_admission is recorded under attempt "
        f"{', '.join(sorted(duplicated))} on this lock; the journal is malformed, refusing to "
        "read the holdout"
    )


def _unresolved_admissions(journal: _Journal) -> frozenset[str]:
    """The admitted attempts that carry no terminal event of their own (§16.17(j)).

    A terminal event is a `validation_abort`, a `validation_verdict_reserved` or a
    `validation_verdict_printed` naming that attempt id; an admission without one is a crash
    between the admission and its record, which the precondition treats as an abort.
    """
    terminal = {str(abort.get("attempt_id")) for abort in journal.aborts}
    terminal.update(journal.verdict_attempts)
    return frozenset(attempt_id for attempt_id in journal.admissions if attempt_id not in terminal)


def _unresolved_clause(unresolved: frozenset[str]) -> str:
    """Name the unresolved attempt ids in a refusal, or say nothing when there are none."""
    if not unresolved:
        return ""
    return f" (unresolved: {', '.join(sorted(unresolved))})"


def _require_covered_abort(journal: _Journal, code_hash: str, config_hash: str) -> None:
    """Admit an attempted lock only on exactly one abort with the founder's covering reset.

    The aborted attempts are the explicit `validation_abort` ids UNION the ids of admissions
    that carry no terminal event (§16.17(j)); a refusal names the unresolved ids so the founder
    can see which attempt has to be reset. §16.17(j) as amended: the reset covers that attempt
    for the attempt's OWN candidate alone — the reset's `(code_hash, config_hash)` and the
    proposed run's pair must BOTH equal the pair recorded on the aborted admission, so an abort
    can never be used to swap candidates under one lock. Every refusal names attempt ids and
    nothing else (R5).
    """
    unresolved = _unresolved_admissions(journal)
    aborted_ids = {str(abort.get("attempt_id")) for abort in journal.aborts} | unresolved
    if len(aborted_ids) != 1:
        raise ValidationRefusedError(
            f"the lock carries {len(aborted_ids)} aborted attempts"
            f"{_unresolved_clause(unresolved)}; exactly one, covered by a founder_reset, is "
            "admissible"
        )
    (aborted,) = aborted_ids
    candidate = _admitted_candidate(journal, aborted)
    covered = any(
        str(reset.get("attempt_id")) == aborted
        and (str(reset.get("code_hash")), str(reset.get("config_hash"))) == candidate
        for reset in journal.resets
    )
    if not covered:
        raise ValidationRefusedError(
            f"no founder_reset for attempt {aborted} names that attempt's own candidate; "
            "refusing to read the holdout"
        )
    if (code_hash, config_hash) != candidate:
        raise ValidationRefusedError(
            f"the proposed candidate is not the one attempt {aborted} was admitted for; "
            "refusing to read the holdout"
        )


def _admitted_candidate(journal: _Journal, attempt_id: str) -> tuple[str, str]:
    """The `(code_hash, config_hash)` the aborted attempt's own admission recorded (§16.17(j)).

    An attempt reaching here carries exactly one admission: a duplicated id is refused earlier
    by `_refuse_duplicate_admissions` (§16.18(b)), so the last event is that one admission.

    Raises:
        ValidationRefusedError: The attempt carries no `validation_admission` on this lock, so
            there is no candidate to hold the reset and the proposed run to.
    """
    recorded = journal.admissions.get(attempt_id, ())
    if not recorded:
        raise ValidationRefusedError(
            f"the aborted attempt {attempt_id} carries no validation_admission on this lock; "
            "refusing to read the holdout"
        )
    admission = recorded[-1]
    return (str(admission.get("code_hash")), str(admission.get("config_hash")))


def record_admission(
    audit_path: Path,
    *,
    lock_sha256: str,
    code_hash: str,
    config_hash: str,
    principal: str,
    session: str,
    run_id: str,
) -> str:
    """Append the `validation_admission` of §16.1 BEFORE execution and return its attempt id.

    The caller runs `check_validation_preconditions` first; this function records the attempt
    exactly as offered, so an admission by a principal the founder did not authorize is durable
    and revokes the lock the moment it prints a verdict (§3.7).

    Raises:
        IntegrityViolationError: The envelope was refused or the append was partial.
    """
    attempt_id = str(uuid4())
    audit_file.append_event(
        audit_path,
        {
            "type": ADMISSION_EVENT,
            "attempt_id": attempt_id,
            "lock": lock_sha256,
            "code_hash": code_hash,
            "config_hash": config_hash,
            "principal": principal,
            "session": session,
            "run_id": run_id,
        },
    )
    return attempt_id


def reserve_verdict(audit_path: Path, attempt_id: str) -> None:
    """Append `validation_verdict_reserved` — written BEFORE the verdict line is printed (§3.7).

    A reservation without a matching `validation_verdict_printed` is treated as printed, so a
    crash between the two never yields a resettable attempt.
    """
    audit_file.append_event(audit_path, {"type": RESERVED_EVENT, "attempt_id": attempt_id})


def record_verdict_printed(audit_path: Path, attempt_id: str, verdict_line: str) -> None:
    """Append `validation_verdict_printed` with the sha256 of the printed line's UTF-8 bytes."""
    audit_file.append_event(
        audit_path,
        {
            "type": PRINTED_EVENT,
            "attempt_id": attempt_id,
            "verdict_sha256": hashlib.sha256(verdict_line.encode("utf-8")).hexdigest(),
        },
    )


def record_abort(audit_path: Path, attempt_id: str, reason: str) -> None:
    """Append `validation_abort` — one abort is resettable by the founder, a second consumes."""
    audit_file.append_event(
        audit_path, {"type": ABORT_EVENT, "attempt_id": attempt_id, "reason": reason}
    )


def seal_aborted_attempt(report_dir: Path) -> Path:
    """Rename an admitted-then-aborted `--validation` attempt's report directory (§3.7/§16.16h).

    The runner calls this after it has written the aborted artifacts of an ADMITTED validation
    attempt: the directory becomes `<run_id>.sealed`, so a later run reading `reports/<run_id>`
    finds nothing and can never mistake a spent attempt's artifacts for its own.

    Args:
        report_dir: The attempt's report directory, named after its run id.

    Returns:
        The sealed directory's path.

    Raises:
        ValidationGuardError: The attempt directory does not exist, or a `<run_id>.sealed`
            directory is already there — an earlier sealed attempt is never overwritten, and
            both directories are left exactly as they were.
    """
    target = report_dir.with_name(f"{report_dir.name}{SEALED_SUFFIX}")
    if not report_dir.is_dir():
        raise ValidationGuardError(
            f"cannot seal {report_dir.name!r}: the aborted attempt's report directory is absent"
        )
    if target.exists():
        raise ValidationGuardError(
            f"cannot seal {report_dir.name!r}: {target.name!r} already exists"
        )
    report_dir.rename(target)
    return target
