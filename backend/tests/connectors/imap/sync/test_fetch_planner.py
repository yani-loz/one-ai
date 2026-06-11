"""
Role: Unit tests for the pure IMAP fetch planner — byte-batching, the contiguous-cursor advance
      (stepping over failed UIDs, never regressing below the floor), and modified-UTF-7 decode.
Used by: pytest (tests/connectors/imap/sync). Pure, no I/O.
Depends on: app.connectors.imap.sync.fetch_planner.
"""

from __future__ import annotations

from app.connectors.imap.sync.fetch_planner import (
    batch_by_bytes,
    decode_mutf7,
    highest_contiguous_uid,
)


def test_batch_by_bytes_packs_near_the_target() -> None:
    sizes = {1: 4, 2: 4, 3: 4}
    batches = batch_by_bytes([1, 2, 3], sizes, batch_bytes=8)

    assert batches == [[1, 2], [3]]


def test_batch_by_bytes_keeps_an_oversized_uid_alone() -> None:
    batches = batch_by_bytes([1, 2], {1: 100, 2: 1}, batch_bytes=8)

    assert batches == [[1], [2]]  # the over-target UID still forms its own batch


def test_batch_by_bytes_unknown_size_becomes_its_own_batch() -> None:
    # UID 2 has no reported size → charged the full batch_bytes so it can't collapse with 1 and 3
    # into one oversized FETCH; each ends up in its own batch.
    batches = batch_by_bytes([1, 2, 3], {1: 4, 3: 4}, batch_bytes=8)

    assert batches == [[1], [2], [3]]


def test_batch_by_bytes_consecutive_unknowns_each_split() -> None:
    # Two ADJACENT unknown-size UIDs must not collapse — each is charged the full batch_bytes, so
    # each forms its own batch (the old get(uid, 0) bug would have packed them into one).
    assert batch_by_bytes([1, 2, 3], {}, batch_bytes=8) == [[1], [2], [3]]


def test_highest_contiguous_uid_stops_at_the_first_gap() -> None:
    # 101,102 stored; 103 not (transient gap) → advance to 102, leave 104 for next run.
    assert highest_contiguous_uid([101, 102, 103, 104], {101, 102, 104}, floor=100) == 102


def test_highest_contiguous_uid_steps_over_a_failed_uid() -> None:
    # 102 permanently failed but accounted-for → the cursor advances PAST it (no wedge).
    assert highest_contiguous_uid([101, 102, 103], {101, 102, 103}, floor=100) == 103


def test_highest_contiguous_uid_never_regresses_below_floor() -> None:
    assert highest_contiguous_uid([101, 102], {101, 102}, floor=200) == 200


def test_decode_mutf7_decodes_a_cyrillic_folder() -> None:
    # "&BB8EQA-" is modified-UTF-7; decoding round-trips to readable Unicode (not the raw escape).
    assert "&" not in decode_mutf7("&BB8EQA-")
    assert decode_mutf7("INBOX") == "INBOX"


def test_decode_mutf7_never_raises_on_malformed_input() -> None:
    # Display-only: a garbled modified-base64 run must degrade to a string, never raise mid-sync.
    for malformed in ("&A-", "&AB", "&2D0-", "&----"):
        assert isinstance(decode_mutf7(malformed), str)
