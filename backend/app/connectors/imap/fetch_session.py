"""
Role: A HELD IMAP connection for incremental fetch — the vendor adapter boundary for the fetch path
      (sibling of client.py's verify_login). Graduates the validated spike's imaplib logic onto an
      async session: connect+login, list folders, SELECT (read-only) + UIDVALIDITY, UID search,
      sizes, and (UID INTERNALDATE BODY.PEEK[]) fetch. The swappable seam tests mock.
Used by: app.connectors.imap.sync.imap_fetcher (drives the session).
Depends on: imaplib + ssl (stdlib), asyncio + a single-thread executor (thread affinity for the
            stateful connection), app.connectors.imap.client (the two vendor errors + params),
            app.connectors.imap.egress_guard (resolve-then-validate-then-dial SSRF defence on the
            real fetch connect), app.connectors.base.incremental_fetch (FetchedMessage).
Key invariants:
  - One IMAP connection is held for the whole session; ALL blocking imaplib calls run on the SAME
    worker thread (a max_workers=1 executor), so the stateful socket is never touched concurrently
    or from two threads. close() shuts the executor + logs out.
  - READ-ONLY: SELECT is readonly; BODY.PEEK[] never sets \\Seen, so the mailbox is untouched.
  - BOUNDED COMMANDS: every UID FETCH compresses its UID list into ranges and chunks it so each
    command line stays far below server limits (Dovecot rejects >64KB lines) — one unbounded
    comma-join used to make any 10k+ first sync permanently unsyncable. Chunk results are merged;
    a failed chunk degrades to missing entries (the planner/runner reconcile), never a raise.
  - TRANSPORT SECURITY: the SSL path verifies certificates (default ssl context); a non-SSL
    connection is upgraded via STARTTLS before LOGIN or refused (client.upgrade_to_starttls) —
    credentials never travel in cleartext.
  - SSRF-SAFE: _default_client_factory resolves + validates the user host (egress_guard) and pins
    the connect to a validated IP BEFORE login — a loopback/private/link-local/metadata target is
    refused, and the rebind-proof IP dial preserves the original hostname for TLS verification. A
    blocked target surfaces as ImapConnectionError (the existing connection-failure path).
  - LIST responses whose mailbox name arrives as a literal (imaplib yields a 2-tuple) are parsed
    to the REAL name, so folders with quotes/8-bit names are synced, not silently skipped.
  - INTERNALDATE is requested and parsed (authoritative received_at for ingest); a parse miss → None
    (ingest falls back to the Received/Date headers), never a raise.
  - The PASSWORD never appears in an exception/log (mapped to ImapAuthError/ImapConnectionError).
"""

from __future__ import annotations

import asyncio
import imaplib
import logging
import re
import ssl
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Protocol

from app.connectors.base.incremental_fetch import FetchedMessage
from app.connectors.imap.client import ImapAuthError, ImapConnectionError, upgrade_to_starttls
from app.connectors.imap.config import ImapConnectionParams
from app.connectors.imap.egress_guard import (
    EgressBlockedError,
    open_guarded_imap4,
    open_guarded_imap4_ssl,
)
from app.connectors.imap.folder_policy import is_excluded_folder

# Raise imaplib's per-response-LINE cap (a process-global; default 1 MB on 3.12). A large mailbox's
# single-line `UID SEARCH <min>:*` reply lists every UID and can top 1 MB; without this it's
# rejected. Message BODIES arrive as literals read via read() and are NOT bound by this cap.
imaplib._MAXLINE = max(imaplib._MAXLINE, 10_000_000)

_LIST_RE = re.compile(rb'^\((?P<flags>.*?)\) (?P<delim>"[^"]*"|NIL) (?P<name>.*)$')
logger = logging.getLogger(__name__)

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
# Cap each UID FETCH sequence-set well below common server command-line limits (Dovecot rejects
# lines over 64KB with BAD) — ~7KB leaves generous headroom for the rest of the command.
_MAX_UID_SET_CHARS = 7000


def _imap_quote(name: str) -> str:
    """Wrap an IMAP mailbox name as a quoted-string, escaping `\\` then `"` (RFC 3501 quoted)."""
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _compress_uid_ranges(uids: list[int]) -> list[str]:
    """Compress a sorted, de-duplicated UID list into RFC 3501 sequence-set atoms (`7`, `5:9`)."""
    atoms: list[str] = []
    start = previous = uids[0]
    for uid in uids[1:]:
        if uid == previous + 1:
            previous = uid
            continue
        atoms.append(str(start) if start == previous else f"{start}:{previous}")
        start = previous = uid
    atoms.append(str(start) if start == previous else f"{start}:{previous}")
    return atoms


def _chunk_uid_set(uids: list[int], max_chars: int = _MAX_UID_SET_CHARS) -> list[str]:
    """Split UIDs into comma-joined sequence-set strings, each at most `max_chars` long.

    Consecutive UIDs collapse into `start:end` ranges first, so a contiguous first sync becomes
    one tiny atom (`1:120000`) instead of 10k+ comma-joined numbers; scattered UIDs are chunked
    by length. Bounding every chunk keeps each `UID FETCH` command line far below server limits
    — one oversized line gets a BAD reply, imaplib raises, and the cursor never advances (the
    mailbox would be permanently unsyncable). Empty input yields no chunks (no command issued).
    """
    if not uids:
        return []
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for atom in _compress_uid_ranges(sorted(set(uids))):
        if current and current_length + len(atom) + 1 > max_chars:
            chunks.append(",".join(current))
            current, current_length = [], 0
        current.append(atom)
        current_length += len(atom) + 1  # +1 for the joining comma (slightly conservative)
    if current:
        chunks.append(",".join(current))
    return chunks


def _flags_skip(flags: bytes) -> bool:
    """True if a folder's LIST flags mark it un-ingestable: \\Noselect (not a real folder) OR an
    RFC 6154 deleted/spam/draft SPECIAL-USE (\\Trash / \\Junk / \\Drafts) — the locale-independent
    half of the ingest blocklist (a non-English Trash folder is still caught by its server flag).
    """
    lowered = flags.lower()
    return b"\\noselect" in lowered or any(
        attr in lowered for attr in (b"\\trash", b"\\junk", b"\\drafts")
    )


def _parse_list_item(item: bytes | tuple[bytes, bytes]) -> str | None:
    """Parse one LIST response item to a selectable folder's RAW name (None = skip).

    A mailbox name containing `"`/`\\`/8-bit bytes arrives as an IMAP LITERAL: imaplib yields a
    (prefix_ending_in_{N}, name_bytes) TUPLE — the real name is the literal bytes, UN-escaped.
    Naively taking item[0] would capture the `{N}` marker as the folder name and the folder would
    silently never sync. A plain bytes line carries the name inline (quoted-string or atom).
    Returns None for \\Noselect AND for SPECIAL-USE deleted/spam/draft folders (ingest blocklist).
    """
    if isinstance(item, tuple):
        prefix, literal_name = item[0], item[1]
        match = _LIST_RE.match(prefix.strip())
        if not match or _flags_skip(match.group("flags")):
            return None
        return literal_name.decode("latin-1")
    match = _LIST_RE.match(item.strip())
    if not match or _flags_skip(match.group("flags")):
        return None
    name = match.group("name").decode("latin-1")
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return name


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
        parsed = [name for name in (_parse_list_item(item) for item in data) if name is not None]
        # Apply the NAME-heuristic half of the ingest blocklist (servers that don't advertise
        # SPECIAL-USE still name their Trash/Junk/Spam/Drafts in the clear). SPECIAL-USE-flagged
        # ones were already dropped in _parse_list_item. Exclusion is policy, not mail loss.
        kept = [name for name in parsed if not is_excluded_folder(name)]
        excluded = len(parsed) - len(kept)
        if excluded:
            logger.info(
                "IMAP: excluded %d Trash/Junk/Spam/Drafts folder(s) from ingest by name policy.",
                excluded,
            )
        return kept

    def _blocking_select(self, folder: str) -> int | None:
        # _blocking_list_folders UN-escapes the mailbox name (strips the wire quoting), so it must
        # be RE-escaped here — a name with an embedded `"` or `\` (e.g. `Projects "Q1"`) would
        # otherwise produce a malformed IMAP quoted-string and select the wrong folder or fail.
        # The name is sent as latin-1 BYTES: _parse_list_item decodes raw wire bytes via latin-1
        # (1:1 byte-preserving), and imaplib's str path encodes commands as ASCII — an 8-bit name
        # (raw-UTF-8 literal) passed as str raises UnicodeEncodeError, which no IMAP handler
        # catches, failing the WHOLE sync. Bytes round-trip the server's exact name; if encoding
        # still fails, skip this one folder (None) — never brick the connection.
        try:
            wire_name = _imap_quote(folder).encode("latin-1")
            typ, _ = self._client.select(wire_name, readonly=True)
        except UnicodeEncodeError:
            return None
        if typ != "OK":
            return None
        _, vals = self._client.response("UIDVALIDITY")
        if not vals or vals[0] is None:
            return None
        # RFC 3501 requires nonzero UIDVALIDITY, but nonconforming servers (older Exchange/
        # Courier) emit 0. Storing 0 would violate ck_sync_cursor_uidvalidity_positive (0014)
        # INSIDE the batch transaction, permanently failing the whole connection's sync — map it
        # to the existing None = "not selectable, skip this folder" contract instead. The skip
        # MUST be loud (cross-vendor review: a silently skipped SELECTABLE folder is ongoing
        # mail loss): warn on every sync so ops sees the recurring degradation. Folder-level
        # health surfacing in the run ledger is tracked in FIX_BEFORE_PROD.
        uidvalidity = int(vals[0])
        if uidvalidity <= 0:
            logger.warning(
                "IMAP folder %r reports UIDVALIDITY %d (RFC 3501 violation) — folder SKIPPED "
                "this sync and EVERY sync until the server is fixed; its mail is NOT ingested.",
                folder,
                uidvalidity,
            )
            return None
        return uidvalidity

    def _blocking_search(self, min_uid: int) -> list[int]:
        typ, data = self._client.uid("SEARCH", "UID", f"{min_uid}:*")
        if typ != "OK" or not data or not data[0]:
            return []
        # 'min:*' always returns at least the highest existing uid even when none are >= min.
        return sorted(uid for uid in (int(u) for u in data[0].split()) if uid >= min_uid)

    def _blocking_sizes(self, uids: list[int]) -> dict[int, int]:
        # Chunked: one bounded UID FETCH per chunk, results merged. A failed chunk contributes no
        # sizes — the planner charges unknown-size UIDs a full batch, so the byte bound still holds.
        sizes: dict[int, int] = {}
        for uid_set in _chunk_uid_set(uids):
            typ, data = self._client.uid("FETCH", uid_set, "(UID RFC822.SIZE)")
            if typ != "OK":
                continue
            for item in data:
                line = item if isinstance(item, bytes) else item[0]
                uid_match = _UID_RE.search(line)
                size_match = _SIZE_VALUE_RE.search(line)
                if uid_match and size_match:  # order-independent: either ordering yields the size
                    sizes[int(uid_match.group(1))] = int(size_match.group(1))
        return sizes

    def _blocking_fetch(self, uids: list[int]) -> list[FetchedMessage]:
        # Chunked: one bounded UID FETCH per chunk, messages concatenated. A failed chunk's UIDs
        # stay unreturned — the runner's requested-vs-returned reconciliation never advances the
        # cursor past them, so they are re-fetched next run (never-lose-mail).
        out: list[FetchedMessage] = []
        for uid_set in _chunk_uid_set(uids):
            typ, data = self._client.uid("FETCH", uid_set, _FETCH_ITEMS)
            if typ != "OK":
                continue
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
    """A factory that opens the real imaplib connection (mapping connect failures to our error).

    The user host is SSRF-validated first (egress_guard) and the connect is pinned to a validated
    IP — a loopback/private/link-local/metadata target is refused before any socket opens, and the
    rebind-proof IP dial preserves the original hostname for TLS verification. A non-SSL connection
    is upgraded via STARTTLS before it is returned (so before any LOGIN); a server without TLS
    raises ImapTlsUnavailableError — credentials never travel in cleartext.
    """

    def _connect() -> imaplib.IMAP4:
        # Resolve + validate then dial the validated IP (defeats DNS-rebind); the openers own the
        # on/off switch. A blocked target (EgressBlockedError) becomes the generic connection
        # failure — which rule matched is never leaked, so a probe gains no signal.
        try:
            if params.use_ssl:
                return open_guarded_imap4_ssl(params, socket_timeout, ssl.create_default_context())
            client = open_guarded_imap4(params, socket_timeout)
        except EgressBlockedError as exc:
            raise ImapConnectionError("Could not reach the IMAP server.") from exc
        except (OSError, imaplib.IMAP4.error) as exc:
            raise ImapConnectionError("Could not reach the IMAP server.") from exc
        upgrade_to_starttls(client)  # closes the socket + raises rather than allow cleartext LOGIN
        return client

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
