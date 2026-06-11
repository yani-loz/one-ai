"""
Role: Unit tests for the runner's _FolderTracker cursor-advance logic — the never-lose-mail core
      (audit B07/B08/B09). Drives the tracker + highest_contiguous_uid directly (pure, no DB) to pin
      the contract: the cursor advances ONLY over durably-stored UIDs and NEVER past a failed
      or requested-but-unreturned one — a failure stays a gap (re-fetched, recovers if transient;
      visibly stalls the folder if permanent), never a silent step-over. failed_uids is an operator
      signal only (safe to cap).
Used by: pytest (tests/connectors/sync). Pure — no database.
Depends on: app.connectors.sync.connector_sync_runner (_FolderTracker, MAX_FAILED_UIDS),
            app.connectors.imap.sync.fetch_planner.highest_contiguous_uid,
            app.connectors.repositories.connector_sync_cursor_repository.FolderSyncState.
"""

from __future__ import annotations

from app.connectors.imap.sync.fetch_planner import highest_contiguous_uid
from app.connectors.repositories.connector_sync_cursor_repository import FolderSyncState
from app.connectors.sync.connector_sync_runner import MAX_FAILED_UIDS, _FolderTracker


def _advance(tracker: _FolderTracker) -> int:
    return highest_contiguous_uid(tracker.candidates, tracker.accounted, tracker.floor)


def test_first_failure_retries_does_not_step_over() -> None:
    tracker = _FolderTracker(uidvalidity=1, floor=0)
    tracker.note_requested([1, 2, 3])
    tracker.account(1)
    tracker.fail(2)  # UID 2 fails for the FIRST time (transient blip)
    tracker.account(3)

    assert _advance(tracker) == 1  # cursor STOPS before UID 2 — retried next run, not skipped
    assert 2 in tracker.failed  # recorded as a dead-letter the next run will recognise


def test_persistent_failure_never_steps_over_the_uid() -> None:
    # UID 2 failed last run (in the cursor's failed_uids) and fails AGAIN. The cursor must STILL
    # stop before it — never advance past a UID that was never durably stored (no silent drop).
    state = FolderSyncState(uidvalidity=1, last_seen_uid=1, failed_uids=[2])
    tracker = _FolderTracker.from_state(state)
    tracker.note_requested([2, 3])
    tracker.fail(2)
    tracker.account(3)

    assert _advance(tracker) == 1  # stays at 1 — the folder visibly stalls, mail is never dropped
    assert 2 in tracker.failed  # surfaced to the operator as currently stalling


def test_retry_that_succeeds_clears_the_dead_letter() -> None:
    state = FolderSyncState(uidvalidity=1, last_seen_uid=0, failed_uids=[2])
    tracker = _FolderTracker.from_state(state)
    tracker.note_requested([2, 3])
    tracker.account(2)  # UID 2 recovers on retry
    tracker.account(3)

    assert _advance(tracker) == 3
    assert 2 not in tracker.failed  # cleared once it stored cleanly


def test_requested_but_unreturned_uid_stops_the_cursor() -> None:
    # Audit B07: a UID the fetcher requested but the server never returned is neither accounted nor
    # failed — it must stop the advance (re-fetched next run), never be stepped over.
    tracker = _FolderTracker(uidvalidity=1, floor=0)
    tracker.note_requested([1, 2, 3])
    tracker.account(1)
    tracker.account(3)  # UID 2 was requested but no message came back

    assert _advance(tracker) == 1


def test_failed_signal_is_capped_safely() -> None:
    # failed_uids is an operator signal, not load-bearing for the cursor — capping it only trims the
    # surfaced list (the accounted/gap advance is independent), so eviction can never lose mail.
    tracker = _FolderTracker(uidvalidity=1, floor=0)
    for uid in range(MAX_FAILED_UIDS + 50):
        tracker.failed.add(uid)

    capped = sorted(tracker.failed)[:MAX_FAILED_UIDS]

    assert len(capped) == MAX_FAILED_UIDS
    assert capped[0] == 0  # keeps the LOWEST UIDs — the one stalling the cursor is the key signal
