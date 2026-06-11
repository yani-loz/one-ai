"""
Role: The OPTIONAL incremental-fetch capability — a Protocol a connector MAY implement (e.g. IMAP),
      kept SEPARATE from BaseConnector so non-fetching / future connectors aren't forced to carry
      dead fetch stubs (BaseConnector's explicit "no dead stubs" invariant). The SyncRunner depends
      on this Protocol + the registry, never on a concrete connector.
Used by: app.connectors.imap.sync.imap_fetcher (implements it), the SyncRunner (consumes it via
         isinstance(connector, SupportsIncrementalFetch)).
Depends on: stdlib only (datetime, typing).
Key invariants:
  - A FetchBatch is byte-bounded (the fetcher packs ~N MB/batch), so memory stays bounded; the
    runner commits per batch. `internal_date` = the IMAP INTERNALDATE, the authoritative
    received_at for ingest, so the fetcher MUST request it (the spike fetched only the body).
  - The cursor is per RAW (modified-UTF-7) folder; fetch_incremental resumes from it and reports
    each batch's observed UIDVALIDITY so the runner detects a reset.
  - NEVER-LOSE-MAIL RECONCILIATION: a FetchBatch reports BOTH the UIDs it REQUESTED
    (`requested_uids`) and the messages it actually got back (`messages`). A server can return
    `OK` with fewer messages than requested (a partial response, a still-existing message the
    parse step dropped, or one expunged mid-cycle). The runner advances its cursor over
    `requested_uids` and treats a requested-but-unreturned UID as an UNACCOUNTED gap that STOPS
    the advance, so it is re-fetched next run instead of being silently skipped. Without the
    requested set the runner could only see returned UIDs and would step the cursor PAST a dropped
    one — permanent silent loss.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class FolderCursor:
    """Where a folder's incremental fetch resumes: its UIDVALIDITY + the last durably-stored UID."""

    uidvalidity: int | None
    last_seen_uid: int


@dataclass(frozen=True, slots=True)
class FetchedMessage:
    """One fetched message: its UID, the raw RFC822 bytes, and the server INTERNALDATE (if any)."""

    uid: int
    raw_bytes: bytes
    internal_date: datetime | None


@dataclass(frozen=True, slots=True)
class FetchBatch:
    """A byte-bounded batch from ONE folder: the UIDs requested + the messages actually returned.

    `requested_uids` is the set the fetcher asked for; `messages` is what came back (possibly
    fewer). The runner reconciles the two so a requested-but-unreturned UID stops the cursor (see
    the module's never-lose-mail invariant) instead of being silently skipped.
    """

    folder: str
    uidvalidity: int
    messages: list[FetchedMessage]
    requested_uids: list[int]


@runtime_checkable
class SupportsIncrementalFetch(Protocol):
    """A connector that can incrementally fetch new items since a per-folder cursor."""

    async def count_pending(self, cursors: Mapping[str, FolderCursor]) -> int:
        """Number of NEW items across all folders since the cursors (the N/M denominator).

        Cheap: a UID-range search per folder (lists of ints, no bodies). The runner shows this as
        the total; it may drift slightly if mail arrives mid-sync, which is acceptable.
        """
        ...

    def fetch_incremental(self, cursors: Mapping[str, FolderCursor]) -> AsyncIterator[FetchBatch]:
        """Stream byte-bounded batches of NEW messages per folder, resuming from `cursors`.

        Contract: yields FetchBatch objects (one folder at a time, sequentially). Each batch's
        messages are NEW (UID > the folder's last_seen_uid). BODY.PEEK[] is used so \\Seen is never
        set; SELECT is read-only. Raises a connector error only on a hard transport failure.
        """
        ...
