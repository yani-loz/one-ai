"""
Role: Unit tests for ImapIncrementalFetcher + ImapConnector's fetch capability — driving a FAKE
      ImapFetchSession (the adapter boundary): cursor-resumed search, UIDVALIDITY-reset re-scan,
      batch streaming + session close, and that ImapConnector satisfies SupportsIncrementalFetch.
Used by: pytest (tests/connectors/imap/sync). No real IMAP.
Depends on: app.connectors.imap.sync.imap_fetcher, app.connectors.imap.connector, the contract.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.connectors.base.incremental_fetch import (
    FetchedMessage,
    FolderCursor,
    SupportsIncrementalFetch,
)
from app.connectors.imap.config import imap_params_from_config
from app.connectors.imap.connector import ImapConnector
from app.connectors.imap.sync.imap_fetcher import ImapIncrementalFetcher

_PARAMS = imap_params_from_config(
    {"host": "mail.example.com", "port": 993, "use_ssl": True, "username": "u@x.com"}
)


class FakeSession:
    """A canned IMAP fetch session: {folder: (uidvalidity, {uid: (size, raw_bytes)})}."""

    def __init__(self, data: Mapping[str, tuple[int, dict[int, tuple[int, bytes]]]]) -> None:
        self._data = data
        self._selected: str | None = None
        self.closed = False

    async def list_folders(self) -> list[str]:
        return list(self._data)

    async def select_folder(self, folder: str) -> int | None:
        self._selected = folder
        return self._data[folder][0]

    async def search_uids(self, min_uid: int) -> list[int]:
        uids = sorted(self._data[self._selected][1])  # type: ignore[index]
        return [u for u in uids if u >= min_uid]

    async def fetch_sizes(self, uids: list[int]) -> dict[int, int]:
        return {u: self._data[self._selected][1][u][0] for u in uids}  # type: ignore[index]

    async def fetch_messages(self, uids: list[int]) -> list[FetchedMessage]:
        return [
            FetchedMessage(uid=u, raw_bytes=self._data[self._selected][1][u][1], internal_date=None)  # type: ignore[index]
            for u in uids
        ]

    async def close(self) -> None:
        self.closed = True


def _fetcher(session: FakeSession, batch_bytes: int = 10_000) -> ImapIncrementalFetcher:
    async def _open(params: object, secret: str) -> FakeSession:
        return session

    return ImapIncrementalFetcher(_PARAMS, "secret", session_opener=_open, batch_bytes=batch_bytes)


async def test_fetch_incremental_streams_all_new_messages_and_closes() -> None:
    session = FakeSession({"INBOX": (10, {1: (5, b"a"), 2: (5, b"b")})})

    batches = [batch async for batch in _fetcher(session).fetch_incremental({})]

    assert len(batches) == 1
    assert batches[0].folder == "INBOX" and batches[0].uidvalidity == 10
    assert [m.uid for m in batches[0].messages] == [1, 2]
    assert session.closed  # the session is always closed


async def test_fetch_incremental_resumes_from_the_cursor() -> None:
    session = FakeSession({"INBOX": (10, {1: (5, b"a"), 2: (5, b"b"), 3: (5, b"c")})})
    cursors = {"INBOX": FolderCursor(uidvalidity=10, last_seen_uid=2)}

    batches = [batch async for batch in _fetcher(session).fetch_incremental(cursors)]

    assert [m.uid for b in batches for m in b.messages] == [3]  # only UID > 2


async def test_fetch_incremental_rescans_from_one_on_uidvalidity_reset() -> None:
    session = FakeSession({"INBOX": (10, {1: (5, b"a"), 2: (5, b"b")})})
    stale = {"INBOX": FolderCursor(uidvalidity=99, last_seen_uid=5)}  # uidvalidity changed

    batches = [batch async for batch in _fetcher(session).fetch_incremental(stale)]

    assert [m.uid for b in batches for m in b.messages] == [1, 2]  # full re-scan


async def test_fetch_incremental_splits_into_byte_bounded_batches() -> None:
    session = FakeSession({"INBOX": (10, {1: (8, b"a"), 2: (8, b"b")})})

    batches = [batch async for batch in _fetcher(session, batch_bytes=8).fetch_incremental({})]

    assert len(batches) == 2  # each message exceeds the per-batch byte target


async def test_count_pending_sums_new_uids() -> None:
    session = FakeSession(
        {"INBOX": (10, {1: (5, b"a"), 2: (5, b"b")}), "Sent": (20, {7: (5, b"z")})}
    )

    assert await _fetcher(session).count_pending({}) == 3


def test_imap_connector_satisfies_supports_incremental_fetch() -> None:
    connector = ImapConnector(_PARAMS, "secret")

    assert isinstance(connector, SupportsIncrementalFetch)
