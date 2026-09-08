"""
Role: The replay selection of contract §16.17(h) as amended by §16.18(c) — given the hidden-test
      budget ledger's events, decide WHICH completed attempt of one `(code_hash, config_hash,
      split_digests)` pair a free repeat replays, and name its reservation id and recorded
      protected-result digest. Pure over the events: it opens no file and reaches no hidden root.
Used by: `tools.mem01_verify.hidden_budget` — `HiddenBudget._free_repeat` loads and verifies the
      one cache file this module names, and its ledger writer takes the two event-type names
      from here so the writer and this projection can never drift apart.
Depends on: `tools.mem01_verify.exceptions` (`HiddenBudgetLedgerError`).
Key invariants:
  - The LAST outcome recorded for a reservation id decides that reservation's state, so a
    completion withdrawn by a later `failed` or `cancelled` is no longer a candidate.
  - Among the pair's reservations whose deciding outcome is `completed`, the selected one is the
    one whose deciding outcome appears LAST in the ledger — COMPLETION order, never reservation
    order (§16.18(c)) — so `reserve R1 -> reserve R2 -> complete R2 -> complete R1` selects R1.
  - The whole ledger is scanned before anything is named, and exactly one digest comes back, so
    the caller opens exactly one cache file and a superseded result stays unread.
  - A deciding `completed` outcome that records no digest is corruption, not a stale entry: it
    raises for every matching reservation of the pair, whether or not it would be selected.
"""

from __future__ import annotations

from collections.abc import Mapping

from tools.mem01_verify.exceptions import HiddenBudgetLedgerError

RESERVATION_EVENT = "hidden_reservation"
OUTCOME_EVENT = "hidden_outcome"

#: The identity of one hidden attempt: candidate pair plus the selected set→digest pairs, sorted.
PairKey = tuple[str, str, tuple[tuple[str, str], ...]]


def pair_key(code_hash: str, config_hash: str, split_digests: Mapping[str, object]) -> PairKey:
    """The identity of a hidden attempt: the candidate pair plus the exact selected digests.

    Args:
        code_hash: The candidate's code hash.
        config_hash: The candidate's config hash.
        split_digests: SET → `split_digest`, as recorded on a reservation or offered by a run.

    Returns:
        A hashable key that is equal for two attempts exactly when they measure the same
        candidate over the same selected splits, independent of the mapping's order.
    """
    pairs = sorted((str(name), str(digest)) for name, digest in split_digests.items())
    return (code_hash, config_hash, tuple(pairs))


def select_replayable_outcome(
    events: list[dict],
    *,
    code_hash: str,
    config_hash: str,
    split_digests: Mapping[str, str],
) -> tuple[str, str] | None:
    """Name the completed attempt whose result a free repeat of this pair replays (§16.18(c)).

    The whole ledger is read first: every outcome is projected to the LAST one recorded per
    reservation id, and among this pair's reservations whose deciding outcome is `completed` the
    winner is the one whose deciding outcome sits latest in the ledger — completion order, so a
    reservation made later but completed earlier never supersedes an earlier one.

    Args:
        events: Every ledger event, in ledger order.
        code_hash: The proposed candidate's code hash.
        config_hash: The proposed candidate's config hash.
        split_digests: SET → `split_digest` for the splits this run selects.

    Returns:
        The `(reservation_id, protected_result_sha256)` of the selected attempt, or `None` when
        this pair has no completed reservation and the run must be charged. Nothing is opened.

    Raises:
        HiddenBudgetLedgerError: A deciding `completed` outcome of this pair records no digest.
    """
    wanted = pair_key(code_hash, config_hash, split_digests)
    deciding = _deciding_outcomes(events)
    selected: tuple[str, str] | None = None
    selected_position = -1
    for event in events:
        digests = event.get("split_digests")
        if event.get("type") != RESERVATION_EVENT or not isinstance(digests, Mapping):
            continue
        if pair_key(str(event.get("code_hash")), str(event.get("config_hash")), digests) != wanted:
            continue
        position, decider = deciding.get(str(event.get("reservation_id")), (-1, {}))
        if decider.get("outcome") != "completed":
            continue
        digest = decider.get("protected_result_sha256")
        if not isinstance(digest, str):
            raise HiddenBudgetLedgerError("a completed outcome carries no result digest")
        if position > selected_position:
            selected = (str(event.get("reservation_id")), digest)
            selected_position = position
    return selected


def _deciding_outcomes(events: list[dict]) -> dict[str, tuple[int, dict]]:
    """Map every reservation id to its LAST outcome event and that event's ledger position."""
    deciding: dict[str, tuple[int, dict]] = {}
    for position, event in enumerate(events):
        if event.get("type") == OUTCOME_EVENT:
            deciding[str(event.get("reservation_id"))] = (position, event)
    return deciding
