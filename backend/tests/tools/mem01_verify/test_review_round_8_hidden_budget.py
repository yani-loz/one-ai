"""
Role: Seals fix-registry row A48 / contract §16.18(c) on the hidden budget's free repeat — the
      result replayed for a pair is the one whose deciding outcome appears LAST in the ledger
      (COMPLETION order, not reservation order): `reserve R1 -> reserve R2 -> complete R2 ->
      complete R1` replays R1's result and leaves R2's cache UNREAD — proven directly (during the
      replay no `open` audit event names R2's cache file, R1's is named at least once, and nothing
      else under the cache directory is opened) and indirectly (a tampered R2 cache is not an
      integrity event for that replay). The last outcome recorded for
      a reservation still decides its state (a completion followed by `cancelled` withdraws that
      reservation), an in-order pair of completions with both retained replays the LATER one
      (never the oldest), a recharged pair replays its latest completion, and a tampered cache of
      the SELECTED result still aborts (§16.17(h)).
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.hidden_budget, .exceptions (imported inside each test through the
      `instrument` loader); tests.tools.mem01_verify.reference (ledger reader, ids, hashes);
      `sys.addaudithook` (the file-open recorder).
Key invariants:
  - Every reservation of the pair shares ONE `(code_hash, config_hash, split_digests)` and only
    `run_id` varies; the tests guard the apparatus inline (a second reservation is a NEW charged
    one; the replay IS a free repeat) so no comparison is vacuous.
  - Only the public surface is used — `reserve`, `record_outcome`, `Reservation.recorded_result`
    — and a cache file is located through the digest on the reservation's last `hidden_outcome`
    event and the documented layout `<results_root>/hidden_budget.results/<digest>.json`.
  - The file-open recorder is this module's own (never imported from another seal): an
    interpreter audit hook on the `open` event (`builtins.open`, `io.open`, `os.open` and
    `io.FileIO` alike; `Path.read_bytes` goes through it), installed once per module and
    recording only while the test's list is attached; the assertion looks only at files under
    `<results_root>/hidden_budget.results/`, so ledger reads may appear and are not its subject,
    and the open COUNT is not the contract — §16.18(c) fixes WHICH cache is read.
  - Markers are nonces inside a nested value of the protected result, never personal data.
"""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import InstrumentLoader

LOCK = hashlib.sha256(b"oracle lock round 8").hexdigest()
CODE = hashlib.sha256(b"candidate code round 8").hexdigest()
CONFIG = hashlib.sha256(b"candidate config round 8").hexdigest()
SPLITS = {
    "QS": hashlib.sha256(b"split QS round 8").hexdigest(),
    "NF": hashlib.sha256(b"split NF round 8").hexdigest(),
}
RESULTS_DIRNAME = "hidden_budget.results"
MARKER_ONE = "oracle-round8-result-one-5a1c"
MARKER_TWO = "oracle-round8-result-two-8d4e"


def _budget(instrument: InstrumentLoader, tmp_path: Path) -> tuple[object, Path, Path]:
    """A budget over an empty ledger under `<tmp>/gold`, results under `<tmp>/hidden`."""
    gold, hidden = tmp_path / "gold", tmp_path / "hidden"
    gold.mkdir()
    hidden.mkdir()
    ledger = gold / "hidden_budget.jsonl"
    ledger.write_bytes(b"")
    budget = instrument("hidden_budget").HiddenBudget(ledger, results_root=hidden)
    return budget, ledger, hidden


def _reserve(budget: object, run_index: int) -> object:
    """One more reservation (or the free repeat) of THE pair; only the run id varies."""
    return budget.reserve(  # type: ignore[attr-defined]
        lock_sha256=LOCK,
        split_digests=SPLITS,
        code_hash=CODE,
        config_hash=CONFIG,
        run_id=reference.oracle_run_id(run_index),
    )


def _block(marker: str, run_index: int) -> dict:
    return {
        "schema": "MEM01_RESULT_V1",
        "status": "PASS",
        "run_id": reference.oracle_run_id(run_index),
        "gates": {"SNAP": {"status": "PASS", "note": marker}},
    }


def _complete(budget: object, reservation: object, block: dict) -> None:
    budget.record_outcome(reservation, outcome="completed", protected_result=block)  # type: ignore[attr-defined]


def _outcomes(ledger: Path) -> list[dict]:
    return [e for e in reference.read_jsonl(ledger) if e.get("type") == "hidden_outcome"]


def _cache_file(ledger: Path, hidden: Path, reservation: object) -> Path:
    """The cache file of a reservation's LAST recorded outcome, by its recorded digest."""
    mine = [
        event
        for event in _outcomes(ledger)
        if event.get("reservation_id") == reservation.reservation_id  # type: ignore[attr-defined]
    ]
    return hidden / RESULTS_DIRNAME / f"{mine[-1]['protected_result_sha256']}.json"


def _tamper(path: Path) -> None:
    assert path.is_file()
    path.write_bytes(b'{"schema": "MEM01_RESULT_V1", "tampered": true}')


def _two_open_reservations(budget: object) -> tuple[object, object]:
    first = _reserve(budget, 1)
    second = _reserve(budget, 2)
    assert first.recorded_result is None and second.recorded_result is None  # type: ignore[attr-defined]
    assert second.reservation_id != first.reservation_id  # type: ignore[attr-defined]  # a NEW charge
    return first, second


def _free_repeat(budget: object, run_index: int) -> object:
    repeat = _reserve(budget, run_index)
    assert repeat.recorded_result is not None, "not a free repeat: the pair differs"  # type: ignore[attr-defined]
    return repeat


def _out_of_order(budget: object) -> tuple[object, object, dict]:
    """reserve R1 -> reserve R2 -> complete R2 -> complete R1; return (R1, R2, R1's block)."""
    first, second = _two_open_reservations(budget)
    block_one = _block(MARKER_ONE, 1)
    _complete(budget, second, _block(MARKER_TWO, 2))  # R2 completes first
    _complete(budget, first, block_one)  # R1 completes LAST
    return first, second, block_one


class _OpenRecorder:
    """A process-wide audit hook keeping the path of every `open` event while a list is attached.

    `sys.addaudithook` sees `builtins.open`, `io.open`, `os.open` and `io.FileIO` alike; hooks
    cannot be removed, so it is installed ONCE per module and records only while `opened` is set.
    """

    installed = False
    opened: list[Path] | None = None

    @classmethod
    def hook(cls, event: str, args: tuple[object, ...]) -> None:
        if event != "open" or cls.opened is None or not args:
            return
        target = args[0]  # str, bytes or PathLike; an int is a descriptor and names no path
        if isinstance(target, str | bytes | os.PathLike):
            cls.opened.append(Path(os.fsdecode(target)))

    @classmethod
    def install_once(cls) -> None:
        if not cls.installed:
            sys.addaudithook(cls.hook)
            cls.installed = True


@contextmanager
def _recording_opens() -> Iterator[list[Path]]:
    """Attach a fresh list to the audit hook for the block; detach it on exit."""
    _OpenRecorder.install_once()
    opened: list[Path] = []
    _OpenRecorder.opened = opened
    try:
        yield opened
    finally:
        _OpenRecorder.opened = None


def _cache_opens(opened: list[Path], hidden: Path) -> list[Path]:
    """The recorded opens that fall under `<results_root>/hidden_budget.results/`, resolved."""
    cache_dir = (hidden / RESULTS_DIRNAME).resolve()
    resolved = [path.resolve() for path in opened]
    return [path for path in resolved if path.parent == cache_dir]


# ── the seal: completion order decides, and the superseded cache stays unread ───────────


def test_the_free_repeat_replays_the_result_completed_last_not_the_reservation_made_last(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    budget, _, _ = _budget(instrument, tmp_path)
    first, _, block_one = _out_of_order(budget)

    repeat = _free_repeat(budget, 3)

    assert dict(repeat.recorded_result) == block_one, (  # type: ignore[attr-defined]
        "the replay chose the reservation made last, not the result completed last"
    )
    assert repeat.reservation_id == first.reservation_id  # type: ignore[attr-defined]


def test_the_superseded_cache_is_never_opened_and_only_the_selected_one_is(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    budget, ledger, hidden = _budget(instrument, tmp_path)
    first, second, block_one = _out_of_order(budget)
    selected, superseded = _cache_file(ledger, hidden, first), _cache_file(ledger, hidden, second)

    with _recording_opens() as opened:
        repeat = _free_repeat(budget, 3)

    cache_opens = _cache_opens(opened, hidden)
    assert superseded.resolve() not in cache_opens, "the replay opened the superseded cache"
    assert selected.resolve() in cache_opens  # at least once: the count is not the contract
    assert set(cache_opens) == {selected.resolve()}  # no other file under the cache directory
    assert dict(repeat.recorded_result) == block_one  # type: ignore[attr-defined]


def test_a_tampered_superseded_cache_does_not_disturb_the_replay(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    exceptions = instrument("exceptions")
    budget, ledger, hidden = _budget(instrument, tmp_path)
    _, second, block_one = _out_of_order(budget)
    _tamper(_cache_file(ledger, hidden, second))  # R2's cache: superseded, must stay unread

    try:
        repeat = _free_repeat(budget, 3)
    except exceptions.HiddenBudgetLedgerError:
        pytest.fail("the replay opened the superseded cache")

    assert dict(repeat.recorded_result) == block_one  # type: ignore[attr-defined]


# ── controls: green today, and they must stay green after the fix ────────────────────────


def test_in_order_completions_of_two_open_reservations_replay_the_later_one(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    budget, ledger, _ = _budget(instrument, tmp_path)
    first, second = _two_open_reservations(budget)
    _complete(budget, first, _block(MARKER_ONE, 1))
    block_two = _block(MARKER_TWO, 2)
    _complete(budget, second, block_two)  # R2 completes LAST; both completions stay recorded

    repeat = _free_repeat(budget, 3)

    assert dict(repeat.recorded_result) == block_two  # type: ignore[attr-defined]  # never the oldest
    assert repeat.reservation_id == second.reservation_id  # type: ignore[attr-defined]
    assert [e["outcome"] for e in _outcomes(ledger)] == ["completed", "completed"]  # both retained


def test_a_later_non_completed_outcome_withdraws_a_reservation_from_the_candidates(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    budget, _, _ = _budget(instrument, tmp_path)
    first, second = _two_open_reservations(budget)
    block_one = _block(MARKER_ONE, 1)
    _complete(budget, first, block_one)
    _complete(budget, second, _block(MARKER_TWO, 2))  # the last completion EVENT is R2's...
    budget.record_outcome(second, outcome="cancelled", protected_result=None)  # type: ignore[attr-defined]

    repeat = _free_repeat(budget, 3)

    assert dict(repeat.recorded_result) == block_one  # type: ignore[attr-defined]  # ...but R2 is withdrawn
    assert repeat.reservation_id == first.reservation_id  # type: ignore[attr-defined]


def test_a_recharged_pair_replays_its_latest_completion(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    budget, _, _ = _budget(instrument, tmp_path)
    first = _reserve(budget, 1)
    _complete(budget, first, _block(MARKER_ONE, 1))
    budget.record_outcome(first, outcome="failed", protected_result=None)  # type: ignore[attr-defined]  # R1 withdrawn
    second = _reserve(budget, 2)  # charged again: a NEW reservation, not a replay of R1
    assert second.recorded_result is None and second.reservation_id != first.reservation_id  # type: ignore[attr-defined]
    block_two = _block(MARKER_TWO, 2)
    _complete(budget, second, block_two)

    repeat = _free_repeat(budget, 3)

    assert dict(repeat.recorded_result) == block_two  # type: ignore[attr-defined]
    assert repeat.reservation_id == second.reservation_id  # type: ignore[attr-defined]


def test_a_tampered_cache_of_the_selected_result_still_aborts(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    exceptions = instrument("exceptions")
    budget, ledger, hidden = _budget(instrument, tmp_path)
    first = _reserve(budget, 1)
    _complete(budget, first, _block(MARKER_ONE, 1))
    _tamper(_cache_file(ledger, hidden, first))  # the ONLY completed result: the selected one

    with pytest.raises(exceptions.HiddenBudgetLedgerError):
        _reserve(budget, 2)
