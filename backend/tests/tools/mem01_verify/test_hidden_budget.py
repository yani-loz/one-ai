"""
Role: Seals the hidden-budget ledger of contract §3.6 / §1.3 `hidden_budget` / §16.1-16.2 on a
      temporary ledger — construction refuses a missing ledger unless creation is requested,
      reservation before evaluation (a durable `hidden_reservation` event with exactly the
      literal keys), the 20th reservation admitted and the 21st refused, the free repeat of a
      completed pair, the `hidden_outcome` event, re-charging after failed/cancelled outcomes,
      counters keyed by split digest across locks, `budget_raise` (raise only, never lower),
      split digests, and atomic concurrent reservations.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.hidden_budget, .audit_file, .exceptions (imported inside each
      test); tests.tools.mem01_verify.reference (ledger reader, id and stamp forms).
Key invariants:
  - Event key sets are asserted EXACTLY as §16.1 lists them (`type`, `event_id`, `at` plus the
    type-specific keys, nothing else). Events the oracle authors carry `at` in the §16.1 form;
    on events the instrument writes only the FORM of `event_id` and `at` is asserted.
"""

from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import InstrumentLoader

LOCK = hashlib.sha256(b"oracle lock").hexdigest()
LOCK_2 = hashlib.sha256(b"oracle lock two").hexdigest()
QS = hashlib.sha256(b"split QS").hexdigest()
NF = hashlib.sha256(b"split NF").hexdigest()
OTHER = hashlib.sha256(b"split other").hexdigest()
BASE_KEYS = {"type", "event_id", "at"}
RESERVATION_KEYS = BASE_KEYS | {
    "reservation_id",
    "lock",
    "split_digests",
    "code_hash",
    "config_hash",
    "run_id",
}
OUTCOME_KEYS = BASE_KEYS | {
    "reservation_id",
    "outcome",
    "protected_result_sha256",
    "protected_result_path",
}


def _pair(index: int) -> tuple[str, str]:
    return (
        hashlib.sha256(f"code {index}".encode()).hexdigest(),
        hashlib.sha256(f"config {index}".encode()).hexdigest(),
    )


def _ledger(tmp_path: Path) -> Path:
    """An existing, empty ledger — the state `release cut --draft` leaves behind (§16.2)."""
    path = tmp_path / "hidden_budget.jsonl"
    path.write_bytes(b"")
    return path


def _reserve(
    budget: object, index: int, *, lock: str = LOCK, splits: dict[str, str] | None = None
) -> object:
    code, config = _pair(index)
    return budget.reserve(  # type: ignore[attr-defined]
        lock_sha256=lock,
        split_digests=splits or {"QS": QS, "NF": NF},
        code_hash=code,
        config_hash=config,
        run_id=reference.oracle_run_id(index),
    )


def _events(ledger: Path, event_type: str) -> list[dict]:
    return [event for event in reference.read_jsonl(ledger) if event.get("type") == event_type]


def _raise_limit(instrument: InstrumentLoader, ledger: Path, split_digest: str, limit: int) -> None:
    instrument("audit_file").append_event(
        ledger,
        {
            "type": "budget_raise",
            "split_digest": split_digest,
            "new_limit": limit,
            "principal": "founder",
            "reason": "oracle raise",
            "at": reference.EVENT_AT,
        },
    )


def _assert_stamped(event: dict) -> None:
    assert re.fullmatch(reference.UUID4_PATTERN, event["event_id"]), event["event_id"]
    assert re.fullmatch(reference.EVENT_AT_PATTERN, event["at"]), event["at"]


def test_default_limit_constant(instrument: InstrumentLoader) -> None:
    assert instrument("hidden_budget").HIDDEN_BUDGET_DEFAULT_LIMIT == 20


def test_missing_ledger_is_refused_at_construction_unless_creation_is_requested(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    hidden_budget = instrument("hidden_budget")
    exceptions = instrument("exceptions")
    missing = tmp_path / "hidden_budget.jsonl"

    with pytest.raises(exceptions.HiddenBudgetLedgerError):
        hidden_budget.HiddenBudget(missing)
    with pytest.raises(exceptions.HiddenBudgetLedgerError):
        hidden_budget.HiddenBudget(missing, create_if_missing=False)
    assert not missing.exists()
    created = hidden_budget.HiddenBudget(missing, create_if_missing=True)

    assert missing.exists() and missing.read_bytes() == b""
    assert created.counters({"QS": QS}).by_split["QS"] == 0


def test_reserve_appends_a_durable_event_with_exactly_the_literal_keys_before_returning(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    hidden_budget = instrument("hidden_budget")
    ledger = _ledger(tmp_path)
    budget = hidden_budget.HiddenBudget(ledger)

    reservation = _reserve(budget, 1)

    events = _events(ledger, "hidden_reservation")
    assert len(events) == 1 and set(events[0]) == RESERVATION_KEYS
    _assert_stamped(events[0])
    assert events[0]["reservation_id"] == reservation.reservation_id
    assert events[0]["lock"] == LOCK and events[0]["split_digests"] == {"QS": QS, "NF": NF}
    assert (events[0]["code_hash"], events[0]["config_hash"]) == _pair(1)
    assert events[0]["run_id"] == reference.oracle_run_id(1)
    assert reservation.recorded_result is None
    assert reservation.counters_before.by_split["QS"] == 0
    counters = budget.counters({"QS": QS, "NF": NF})
    assert counters.by_split == {"QS": 1, "NF": 1, "LANG": 0, "RET": 0}
    assert counters.total == 1 and counters.limit == 20


def test_twentieth_reservation_is_admitted_and_twenty_first_refused(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    hidden_budget = instrument("hidden_budget")
    exceptions = instrument("exceptions")
    ledger = _ledger(tmp_path)
    budget = hidden_budget.HiddenBudget(ledger)
    for index in range(1, 20):
        _reserve(budget, index)

    twentieth = _reserve(budget, 20)
    with pytest.raises(exceptions.HiddenBudgetExhaustedError):
        _reserve(budget, 21)

    assert twentieth.counters_before.by_split["QS"] == 19
    counters = budget.counters({"QS": QS})
    assert counters.by_split["QS"] == 20 and counters.total == 20 and counters.limit == 20
    assert len(_events(ledger, "hidden_reservation")) == 20


def test_completed_pair_repeats_for_free_and_records_a_hidden_outcome_event(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    hidden_budget = instrument("hidden_budget")
    ledger = _ledger(tmp_path)
    budget = hidden_budget.HiddenBudget(ledger, results_root=tmp_path / "hidden")
    first = _reserve(budget, 7)
    protected = {
        "schema": "MEM01_RESULT_V1",
        "status": "FAIL",
        "run_id": reference.oracle_run_id(7),
    }

    budget.record_outcome(first, outcome="completed", protected_result=protected)
    repeat = _reserve(budget, 7)

    assert dict(repeat.recorded_result) == protected
    assert budget.counters({"QS": QS}).by_split["QS"] == 1
    assert len(_events(ledger, "hidden_reservation")) == 1
    outcomes = _events(ledger, "hidden_outcome")
    assert len(outcomes) == 1 and set(outcomes[0]) == OUTCOME_KEYS
    _assert_stamped(outcomes[0])
    assert outcomes[0]["reservation_id"] == first.reservation_id
    assert outcomes[0]["outcome"] == "completed"
    assert re.fullmatch(reference.HEX64_PATTERN, outcomes[0]["protected_result_sha256"])
    recorded_path = outcomes[0]["protected_result_path"]
    # §16.13: relative to the report root when recorded
    assert recorded_path is None or (
        isinstance(recorded_path, str) and not Path(recorded_path).is_absolute()
    )


@pytest.mark.parametrize("outcome", ["failed", "cancelled"])
def test_failed_or_cancelled_pair_is_charged_again_on_re_execution(
    instrument: InstrumentLoader, tmp_path: Path, outcome: str
) -> None:
    hidden_budget = instrument("hidden_budget")
    ledger = _ledger(tmp_path)
    budget = hidden_budget.HiddenBudget(ledger)
    first = _reserve(budget, 3)

    budget.record_outcome(first, outcome=outcome, protected_result=None)
    again = _reserve(budget, 3)

    assert again.recorded_result is None
    assert budget.counters({"QS": QS}).by_split["QS"] == 2
    assert len(_events(ledger, "hidden_reservation")) == 2
    outcomes = _events(ledger, "hidden_outcome")
    assert [event["outcome"] for event in outcomes] == [outcome]
    assert set(outcomes[0]) == OUTCOME_KEYS
    assert outcomes[0]["protected_result_sha256"] is None
    assert outcomes[0]["protected_result_path"] is None  # §16.13: null on failed/cancelled


def test_counters_are_keyed_by_split_digest_across_locks(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    hidden_budget = instrument("hidden_budget")
    budget = hidden_budget.HiddenBudget(_ledger(tmp_path))
    first = _reserve(budget, 1, lock=LOCK)

    second = _reserve(budget, 2, lock=LOCK_2)  # a superseding release, same test/QS
    third = _reserve(budget, 3, lock=LOCK_2, splits={"QS": OTHER})  # a new digest

    assert budget.counters({"QS": QS}).by_split["QS"] == 2
    assert budget.counters({"QS": OTHER}).by_split["QS"] == 1
    # the lock-local invocation count each reservation saw: 0 under a fresh lock, then 1
    assert first.counters_before.invocations_under_lock == 0
    assert second.counters_before.invocations_under_lock == 0
    assert third.counters_before.invocations_under_lock == 1


def test_budget_raise_lifts_the_limit_and_never_lowers_or_resets(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    hidden_budget = instrument("hidden_budget")
    exceptions = instrument("exceptions")
    ledger = _ledger(tmp_path)
    budget = hidden_budget.HiddenBudget(ledger)
    for index in range(1, 21):
        _reserve(budget, index, splits={"QS": QS})
    with pytest.raises(exceptions.HiddenBudgetExhaustedError):
        _reserve(budget, 21, splits={"QS": QS})

    _raise_limit(instrument, ledger, QS, 25)
    twenty_first = _reserve(budget, 21, splits={"QS": QS})
    _raise_limit(instrument, ledger, QS, 10)
    twenty_second = _reserve(budget, 22, splits={"QS": QS})

    counters = budget.counters({"QS": QS})
    assert twenty_first.counters_before.by_split["QS"] == 20
    assert twenty_second.counters_before.by_split["QS"] == 21
    assert counters.by_split["QS"] == 22 and counters.limit == 25
    raises = _events(ledger, "budget_raise")
    assert [event["new_limit"] for event in raises] == [25, 10]
    assert all(
        set(event) == BASE_KEYS | {"split_digest", "new_limit", "principal", "reason"}
        for event in raises
    )


def test_split_digest_depends_only_on_that_sets_hidden_test_entries(
    instrument: InstrumentLoader,
) -> None:
    hidden_budget = instrument("hidden_budget")
    files = {
        "hidden/test/QS/part0.jsonl": {
            "sha256": "a" * 64,
            "bytes": 10,
            "records": 2,
            "visibility": "hidden",
        },
        "hidden/test/QS/part1.jsonl": {
            "sha256": "b" * 64,
            "bytes": 11,
            "records": 3,
            "visibility": "hidden",
        },
        "hidden/test/NF/part0.jsonl": {
            "sha256": "c" * 64,
            "bytes": 12,
            "records": 1,
            "visibility": "hidden",
        },
        "data/optimization/QS.jsonl": {
            "sha256": "d" * 64,
            "bytes": 13,
            "records": 4,
            "visibility": "visible",
        },
    }
    manifest = {"release_name": "step1-gold-v1", "files": files}
    reordered = {"release_name": "other-name", "files": dict(reversed(list(files.items())))}
    nf_changed = {
        "release_name": "step1-gold-v1",
        "files": {
            **files,
            "hidden/test/NF/part0.jsonl": {
                **files["hidden/test/NF/part0.jsonl"],
                "sha256": "e" * 64,
            },
        },
    }
    qs_changed = {
        "release_name": "step1-gold-v1",
        "files": {
            **files,
            "hidden/test/QS/part1.jsonl": {**files["hidden/test/QS/part1.jsonl"], "records": 4},
        },
    }

    digest = hidden_budget.split_digest(manifest, "QS")

    assert len(digest) == 64 and digest == digest.lower()
    assert hidden_budget.split_digest(reordered, "QS") == digest
    assert hidden_budget.split_digest(nf_changed, "QS") == digest
    assert hidden_budget.split_digest(qs_changed, "QS") != digest
    assert hidden_budget.split_digest(manifest, "NF") != digest


def test_concurrent_reservations_never_exceed_the_limit(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    hidden_budget = instrument("hidden_budget")
    exceptions = instrument("exceptions")
    ledger = _ledger(tmp_path)

    def attempt(index: int) -> bool:
        budget = hidden_budget.HiddenBudget(ledger)
        try:
            _reserve(budget, 100 + index, splits={"QS": QS})
            return True
        except exceptions.HiddenBudgetExhaustedError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(40)))

    assert outcomes.count(True) == 20 and outcomes.count(False) == 20
    assert len(_events(ledger, "hidden_reservation")) == 20
    assert hidden_budget.HiddenBudget(ledger).counters({"QS": QS}).by_split["QS"] == 20


def test_torn_ledger_is_a_ledger_or_integrity_error(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    hidden_budget = instrument("hidden_budget")
    exceptions = instrument("exceptions")
    ledger = tmp_path / "hidden_budget.jsonl"
    ledger.write_bytes(b'{"type": "hidden_reservation", "torn": ')

    with pytest.raises((exceptions.HiddenBudgetLedgerError, exceptions.IntegrityViolationError)):
        _reserve(hidden_budget.HiddenBudget(ledger), 1)


def test_budget_errors_are_mem01_errors(instrument: InstrumentLoader) -> None:
    exceptions = instrument("exceptions")

    assert issubclass(exceptions.HiddenBudgetExhaustedError, exceptions.Mem01Error)
    assert issubclass(exceptions.HiddenBudgetLedgerError, exceptions.Mem01Error)
