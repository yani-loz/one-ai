"""
Role: ImapIncrementalFetcher — the SupportsIncrementalFetch implementation: drives a held
      ImapFetchSession through each folder (SELECT read-only → UID search since the cursor →
      byte-batched fetch) and streams FetchBatch objects. Sequential, one connection per mailbox.
Used by: app.connectors.imap.connector.ImapConnector (delegates its fetch capability here).
Depends on: app.connectors.base.incremental_fetch (the contract), app.connectors.imap.fetch_session
            (the held-connection seam; injectable for tests), .fetch_planner (batch_by_bytes),
            app.connectors.imap.config (ImapConnectionParams).
Key invariants:
  - SEQUENTIAL folders, ONE connection — friendlier to provider rate limits and matches the runner's
    single-writer/per-batch-commit model. Within a folder, fetch STREAMS in byte-bounded batches.
  - A folder whose stored UIDVALIDITY no longer matches (server reset) is re-scanned from UID 1 (old
    UIDs are meaningless after a reset); a brand-new folder also scans from 1. Else from last+1.
  - Always opens its OWN session and closes it in a finally — no connection leaks across a sync.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping

from app.connectors.base.incremental_fetch import FetchBatch, FolderCursor
from app.connectors.imap.config import ImapConnectionParams
from app.connectors.imap.fetch_session import ImapFetchSession, open_imap_session
from app.connectors.imap.sync.fetch_planner import batch_by_bytes

_DEFAULT_BATCH_BYTES = 10 * 1024 * 1024  # ~10 MB per FETCH batch (the spike's proven size)

SessionOpener = Callable[[ImapConnectionParams, str], Awaitable[ImapFetchSession]]


class ImapIncrementalFetcher:
    """Streams new IMAP messages per folder since a cursor, over a held connection."""

    def __init__(
        self,
        params: ImapConnectionParams,
        secret: str,
        session_opener: SessionOpener | None = None,
        batch_bytes: int = _DEFAULT_BATCH_BYTES,
    ) -> None:
        """Hold the connection params + secret; default to the real session opener."""
        self._params = params
        self._secret = secret
        self._open = session_opener or open_imap_session
        self._batch_bytes = batch_bytes

    async def count_pending(self, cursors: Mapping[str, FolderCursor]) -> int:
        """Count NEW UIDs across folders since the cursors (the N/M denominator; no bodies)."""
        session = await self._open(self._params, self._secret)
        try:
            total = 0
            for folder in await session.list_folders():
                uidvalidity = await session.select_folder(folder)
                if uidvalidity is None:
                    continue
                total += len(await session.search_uids(self._min_uid(cursors, folder, uidvalidity)))
            return total
        finally:
            await session.close()

    async def fetch_incremental(
        self, cursors: Mapping[str, FolderCursor]
    ) -> AsyncIterator[FetchBatch]:
        """Stream byte-bounded batches of NEW messages, folder by folder, from the cursors."""
        session = await self._open(self._params, self._secret)
        try:
            for folder in await session.list_folders():
                uidvalidity = await session.select_folder(folder)
                if uidvalidity is None:
                    continue
                uids = await session.search_uids(self._min_uid(cursors, folder, uidvalidity))
                if not uids:
                    continue
                sizes = await session.fetch_sizes(uids)
                for batch_uids in batch_by_bytes(uids, sizes, self._batch_bytes):
                    messages = await session.fetch_messages(batch_uids)
                    # Carry the REQUESTED uids alongside the returned messages so the runner can
                    # detect a requested-but-unreturned UID (a partial/dropped FETCH) and NOT
                    # advance the cursor past it — the never-lose-mail reconciliation.
                    yield FetchBatch(
                        folder=folder,
                        uidvalidity=uidvalidity,
                        messages=messages,
                        requested_uids=batch_uids,
                    )
        finally:
            await session.close()

    @staticmethod
    def _min_uid(cursors: Mapping[str, FolderCursor], folder: str, uidvalidity: int) -> int:
        """First UID to fetch: last+1 if the cursor's UIDVALIDITY matches, else 1 (reset)."""
        cursor = cursors.get(folder)
        if cursor is None or cursor.uidvalidity != uidvalidity:
            return 1
        return cursor.last_seen_uid + 1
