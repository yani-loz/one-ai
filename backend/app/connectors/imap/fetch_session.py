"""
Role: A HELD IMAP connection for incremental fetch — the vendor adapter boundary for the fetch path
      (sibling of client.py's verify_login). Graduates the validated spike's imaplib logic onto an
      async session: connect+login, list folders, SELECT (read-only) + UIDVALIDITY, UID search,
      sizes, and (UID INTERNALDATE BODY.PEEK[]) fetch. The swappable seam tests mock.
Used by: app.connectors.imap.sync.imap_fetcher (drives the session).
Depends on: imaplib + ssl (stdlib), asyncio + a single-thread executor (thread affinity for the
            stateful connection), app.connectors.imap.client (the two vendor errors + params),
            app.connectors.base.incremental_fetch (FetchedMessage).
Key invariants:
  - One IMAP connection is held for the whole session; ALL blocking imaplib calls run on the SAME
    worker thread (a max_workers=1 executor), so the stateful socket is never touched concurrently
    or from two threads. close() shuts the executor + logs out.
  - READ-ONLY: SELECT is readonly; BODY.PEEK[] never sets \\Seen, so the mailbox is untouched.
  - INTERNALDATE is requested and parsed (authoritative received_at for ingest); a parse miss → None
    (ingest falls back to the Received/Date headers), never a raise.
  - The PASSWORD never appears in an exception/log (mapped to ImapAuthError/ImapConnectionError).
"""

from __future__ import annotations

import asyncio
import imaplib
import re
import ssl
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Protocol

from app.connectors.base.incremental_fetch import FetchedMessage
from app.connectors.imap.client import ImapAuthError, ImapConnectionError
from app.connectors.imap.config import ImapConnectionParams

# Raise imaplib's per-response-LINE cap (a process-global; default 1 MB on 3.12). A large mailbox's
# single-line `UID SEARCH <min>:*` reply lists every UID and can top 1 MB; without this it's
# rejected. Message BODIES arrive as literals read via read() and are NOT bound by this cap.
imaplib._MAXLINE = max(imaplib._MAXLINE, 10_000_000)

_LIST_RE = re.compile(rb'^\((?P<flags>.*?)\) (?P<delim>"[^"]*"|NIL) (?P<name>.*)$')
_UID_RE = re.compile(rb"UID (\d+)", re.IGNORECASE)
# UID and RFC822.SIZE are matched SEPARATELY so either server ordering of the FETCH data items
# (`UID .. RFC822.SIZE ..` or `RFC822.SIZE .. UID ..`, both RFC-3501-legal) yields the size.
_SIZE_VALUE_RE = re.compile(rb"RFC822\.SIZE (\d+)", re.IGNORECASE)
# INTERNALDATE = `dd-Mon-yyyy hh:mm:ss +zzzz`; parsed by component (NOT strptime %b, which is
# locale-dependent — a non-English LC_TIME would reject the always-English month names).
_INTERNALDATE_RE = re.compile(
    rb'INTERNALDATE "\s*(\d{1,2})-([A-Za-z]{3})-(\d{4}) (\d{2}):(\d{2}):(\d{2}) ([+-]\d{4})"',
    re.IGNORECASE,
)
_MONTHS = {
    month: number
    for number, month in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}
_FETCH_ITEMS = "(UID INTERNALDATE BODY.PEEK[])"
_DEFAULT_TIMEOUT = 60.0


def _imap_quote(name: str) -> str:
    """Wrap an IMAP mailbox name as a quoted-string, escaping `\\` then `"` (RFC 3501 quoted)."""
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _parse_internaldate(raw: bytes) -> datetime | None:
    """Parse an IMAP INTERNALDATE to an aware datetime, locale-independently (None if bad)."""
    match = _INTERNALDATE_RE.search(raw)
    if match is None:
        return None
    try:
        day, mon, year, hour, minute, second, offset = (group.decode() for group in match.groups())
        month = _MONTHS.get(mon.lower())
        if month is None:
            return None
        sign = 1 if offset[0] == "+" else -1
        zone = timezone(sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5])))
        return datetime(
            int(year), month, int(day), int(hour), int(minute), int(second), tzinfo=zone
        )
    except (ValueError, UnicodeDecodeError):
        return None


class ImapFetchSession(Protocol):
    """The held-connection verbs the incremental fetcher needs (the mockable vendor seam)."""

    async def list_folders(self) -> list[str]:
        """Return selectable folders' RAW (modified-UTF-7) names."""
        ...

    async def select_folder(self, folder: str) -> int | None:
        """SELECT a folder read-only; return its UIDVALIDITY, or None if not selectable."""
        ...

    async def search_uids(self, min_uid: int) -> list[int]:
        """Return UIDs >= min_uid in the selected folder, sorted."""
        ...

    async def fetch_sizes(self, uids: list[int]) -> dict[int, int]:
        """Return {uid: RFC822.SIZE} for the given UIDs."""
        ...

    async def fetch_messages(self, uids: list[int]) -> list[FetchedMessage]:
        """Fetch (UID INTERNALDATE BODY.PEEK[]) for the UIDs; return parsed messages."""
        ...

    async def close(self) -> None:
        """Log out + release the worker thread."""
        ...


class DefaultImapFetchSession:
    """Real held IMAP connection; every blocking call on a single dedicated worker thread."""

    def __init__(self, client: imaplib.IMAP4, executor: ThreadPoolExecutor) -> None:
        """Hold the connected+authenticated imaplib client + its single-thread executor."""
        self._client = client
        self._executor = executor

    async def _run(self, func, *args):  # type: ignore[no-untyped-def]
        """Run a blocking imaplib call on the session's dedicated thread."""
        return await asyncio.get_running_loop().run_in_executor(self._executor, func, *args)

    async def list_folders(self) -> list[str]:
        return await self._run(self._blocking_list_folders)

    async def select_folder(self, folder: str) -> int | None:
        return await self._run(self._blocking_select, folder)

    async def search_uids(self, min_uid: int) -> list[int]:
        return await self._run(self._blocking_search, min_uid)

    async def fetch_sizes(self, uids: list[int]) -> dict[int, int]:
        return await self._run(self._blocking_sizes, uids)

    async def fetch_messages(self, uids: list[int]) -> list[FetchedMessage]:
        return await self._run(self._blocking_fetch, uids)

    async def close(self) -> None:
        # finally: the worker thread MUST be released even if the logout await is cancelled, else
        # the single-thread executor leaks (mirrors open_imap_session's executor teardown).
        try:
            await self._run(self._blocking_logout)
        finally:
            self._executor.shutdown(wait=False)

    # ── blocking imaplib (runs on the dedicated worker thread) ──
    def _blocking_list_folders(self) -> list[str]:
        typ, data = self._client.list()
        if typ != "OK":
            return []
        names: list[str] = []
        for item in data:
            line = item if isinstance(item, bytes) else item[0]
            match = _LIST_RE.match(line.strip())
            if not match or b"\\noselect" in match.group("flags").lower():
                continue
            name = match.group("name").decode("latin-1")
            if name.startswith('"') and name.endswith('"'):
                name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            names.append(name)
        return names

    def _blocking_select(self, folder: str) -> int | None:
        # _blocking_list_folders UN-escapes the mailbox name (strips the wire quoting), so it must
        # be RE-escaped here — a name with an embedded `"` or `\` (e.g. `Projects "Q1"`) would
        # otherwise produce a malformed IMAP quoted-string and select the wrong folder or fail.
        typ, _ = self._client.select(_imap_quote(folder), readonly=True)
        if typ != "OK":
            return None
        _, vals = self._client.response("UIDVALIDITY")
        if not vals or vals[0] is None:
            return None
        return int(vals[0])

    def _blocking_search(self, min_uid: int) -> list[int]:
        typ, data = self._client.uid("SEARCH", "UID", f"{min_uid}:*")
        if typ != "OK" or not data or not data[0]:
            return []
        # 'min:*' always returns at least the highest existing uid even when none are >= min.
        return sorted(uid for uid in (int(u) for u in data[0].split()) if uid >= min_uid)

    def _blocking_sizes(self, uids: list[int]) -> dict[int, int]:
        typ, data = self._client.uid("FETCH", ",".join(map(str, uids)), "(UID RFC822.SIZE)")
        sizes: dict[int, int] = {}
        if typ != "OK":
            return sizes
        for item in data:
            line = item if isinstance(item, bytes) else item[0]
            uid_match = _UID_RE.search(line)
            size_match = _SIZE_VALUE_RE.search(line)
            if uid_match and size_match:  # order-independent: either item ordering yields the size
                sizes[int(uid_match.group(1))] = int(size_match.group(1))
        return sizes

    def _blocking_fetch(self, uids: list[int]) -> list[FetchedMessage]:
        typ, data = self._client.uid("FETCH", ",".join(map(str, uids)), _FETCH_ITEMS)
        out: list[FetchedMessage] = []
        if typ != "OK":
            return out
        for item in data:
            if not isinstance(item, tuple) or len(item) != 2:
                continue
            meta, raw = item
            match = _UID_RE.search(meta)
            if match and raw is not None:
                out.append(
                    FetchedMessage(
                        uid=int(match.group(1)),
                        raw_bytes=raw,
                        internal_date=_parse_internaldate(meta),
                    )
                )
        return out

    def _blocking_logout(self) -> None:
        try:
            self._client.logout()
        except Exception:  # logout failure must not mask the sync outcome
            pass


ClientFactory = Callable[[], imaplib.IMAP4]


def _default_client_factory(params: ImapConnectionParams, socket_timeout: float) -> ClientFactory:
    """A factory that opens the real imaplib connection (mapping connect failures to our error)."""

    def _connect() -> imaplib.IMAP4:
        try:
            if params.use_ssl:
                return imaplib.IMAP4_SSL(
                    params.host,
                    params.port,
                    ssl_context=ssl.create_default_context(),
                    timeout=socket_timeout,
                )
            return imaplib.IMAP4(params.host, params.port, timeout=socket_timeout)
        except (OSError, imaplib.IMAP4.error) as exc:
            raise ImapConnectionError("Could not reach the IMAP server.") from exc

    return _connect


def _safe_close(client: imaplib.IMAP4) -> None:
    """Close a connected client best-effort (logout, else low-level shutdown); never raises."""
    try:
        client.logout()
    except Exception:
        try:
            client.shutdown()
        except Exception:
            pass


async def open_imap_session(
    params: ImapConnectionParams,
    secret: str,
    socket_timeout: float = _DEFAULT_TIMEOUT,
    client_factory: ClientFactory | None = None,
) -> DefaultImapFetchSession:
    """Open + authenticate a held IMAP session on a dedicated worker thread.

    Raises ImapConnectionError (unreachable/timeout) or ImapAuthError (bad creds) — secret-free. A
    client that connected before a LOGIN failure is CLOSED, so a bad-credential retry never leaks a
    socket. `client_factory` is injectable for tests (default = the real imaplib connection).
    """
    factory = client_factory or _default_client_factory(params, socket_timeout)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="imap-sync")

    def _connect_login() -> imaplib.IMAP4:
        client = factory()  # raises ImapConnectionError on connect failure (nothing to close yet)
        try:
            client.login(params.username, secret)
        except imaplib.IMAP4.error as exc:  # secret never included
            _safe_close(client)  # close the connected socket before surfacing the auth failure
            raise ImapAuthError("IMAP authentication failed.") from exc
        except OSError as exc:
            _safe_close(client)
            raise ImapConnectionError("IMAP connection error during login.") from exc
        return client

    try:
        client = await asyncio.get_running_loop().run_in_executor(executor, _connect_login)
    except BaseException:
        executor.shutdown(wait=False)
        raise
    return DefaultImapFetchSession(client, executor)
