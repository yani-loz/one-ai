"""
Role: Unit tests for the IMAP fetch session — open_imap_session's failure handling (a client that
      connected before a LOGIN failure must be CLOSED; errors stay secret-free), the bounded/merged
      UID FETCH chunking (one unbounded comma-join made 10k+ mailboxes permanently unsyncable),
      literal LIST-tuple parsing (quoted/8-bit folder names), and the STARTTLS-or-refuse upgrade on
      the use_ssl=false path. The imaplib client is injected or monkeypatched (the vendor boundary).
Used by: pytest (tests/connectors/imap). No real IMAP server.
Depends on: app.connectors.imap.fetch_session, app.connectors.imap.client (the vendor errors).
"""

from __future__ import annotations

import imaplib
import ssl
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from app.connectors.imap.client import ImapAuthError, ImapConnectionError, ImapTlsUnavailableError
from app.connectors.imap.config import imap_params_from_config
from app.connectors.imap.fetch_session import (
    _MAX_UID_SET_CHARS,
    DefaultImapFetchSession,
    _chunk_uid_set,
    _imap_quote,
    _parse_internaldate,
    open_imap_session,
)

_PARAMS = imap_params_from_config(
    {"host": "mail.example.com", "port": 993, "use_ssl": True, "username": "u@x.com"}
)
_PLAIN_PARAMS = imap_params_from_config(
    {"host": "mail.example.com", "port": 143, "use_ssl": False, "username": "u@x.com"}
)


class _FakeClient:
    """A fake imaplib client whose login raises, recording whether it was then closed."""

    def __init__(self, login_error: Exception) -> None:
        self._login_error = login_error
        self.closed = False

    def login(self, username: str, secret: str) -> None:
        raise self._login_error

    def logout(self) -> None:
        self.closed = True


async def test_open_imap_session_closes_client_on_auth_failure() -> None:
    fake = _FakeClient(imaplib.IMAP4.error("login rejected"))

    with pytest.raises(ImapAuthError) as exc_info:
        await open_imap_session(_PARAMS, "super-secret-pw", client_factory=lambda: fake)

    assert fake.closed  # the connected socket was logged out, not leaked
    assert "super-secret-pw" not in str(exc_info.value)  # the secret never leaks into the error


async def test_open_imap_session_closes_client_on_login_oserror() -> None:
    fake = _FakeClient(OSError("dropped mid-login"))

    with pytest.raises(ImapConnectionError):
        await open_imap_session(_PARAMS, "pw", client_factory=lambda: fake)

    assert fake.closed


async def test_open_imap_session_propagates_a_connect_failure() -> None:
    def _failing_factory() -> imaplib.IMAP4:
        raise ImapConnectionError("Could not reach the IMAP server.")

    with pytest.raises(ImapConnectionError):
        await open_imap_session(_PARAMS, "pw", client_factory=_failing_factory)


class _FlippedSizeClient:
    """A client whose FETCH returns RFC822.SIZE BEFORE UID for one item, UID-first for the other."""

    def uid(self, command: str, uids: str, items: str) -> tuple[str, list[bytes]]:
        return ("OK", [b"1 (RFC822.SIZE 1234 UID 5)", b"2 (UID 6 RFC822.SIZE 99)"])


async def test_fetch_sizes_parses_either_data_item_order() -> None:
    # RFC 3501 lets the server order FETCH data items freely; sizing must survive SIZE-before-UID,
    # else every message degrades to its own 1-message FETCH (a throughput cliff).
    executor = ThreadPoolExecutor(max_workers=1)
    session = DefaultImapFetchSession(_FlippedSizeClient(), executor)  # type: ignore[arg-type]
    try:
        assert await session.fetch_sizes([5, 6]) == {5: 1234, 6: 99}
    finally:
        executor.shutdown(wait=False)


def test_parse_internaldate_is_locale_independent() -> None:
    # INTERNALDATE months are always English; parsing must NOT use strptime %b (locale-dependent),
    # so a non-English LC_TIME deployment still yields the authoritative received_at + its offset.
    parsed = _parse_internaldate(b'1 (UID 5 INTERNALDATE "03-Mar-2023 09:30:00 +0200" BODY[] {2}')

    assert parsed is not None
    assert (parsed.year, parsed.month, parsed.day, parsed.hour, parsed.minute) == (
        2023,
        3,
        3,
        9,
        30,
    )
    assert parsed.utcoffset() == timedelta(hours=2)


def test_parse_internaldate_returns_none_on_garbage() -> None:
    assert _parse_internaldate(b'INTERNALDATE "not a date"') is None


def test_imap_quote_re_escapes_quote_and_backslash() -> None:
    # The folder name is UN-escaped by list_folders, so SELECT must re-escape `"` and `\`; else a
    # name like `Projects "Q1"` makes a malformed quoted-string and selects the wrong folder.
    assert _imap_quote("INBOX") == '"INBOX"'
    assert _imap_quote('Projects "Q1"') == '"Projects \\"Q1\\""'
    assert _imap_quote("a\\b") == '"a\\\\b"'


# ── UID FETCH chunking (one unbounded comma-join → server BAD → mailbox unsyncable) ──
def _expand_uid_set(uid_set: str) -> list[int]:
    """Expand an RFC 3501 sequence-set string back into the individual UIDs it covers."""
    uids: list[int] = []
    for atom in uid_set.split(","):
        if ":" in atom:
            start, end = atom.split(":", 1)
            uids.extend(range(int(start), int(end) + 1))
        else:
            uids.append(int(atom))
    return uids


def test_chunk_uid_set_compresses_consecutive_uids_into_ranges() -> None:
    assert _chunk_uid_set([1, 2, 3, 5, 7, 8, 9]) == ["1:3,5,7:9"]


def test_chunk_uid_set_bounds_every_chunk_and_loses_no_uid() -> None:
    uids = list(range(101, 200, 2))  # scattered — no compressible runs

    chunks = _chunk_uid_set(uids, max_chars=16)

    assert len(chunks) > 1
    assert all(len(chunk) <= 16 for chunk in chunks)
    assert [uid for chunk in chunks for uid in _expand_uid_set(chunk)] == uids


def test_chunk_uid_set_empty_input_issues_no_commands() -> None:
    assert _chunk_uid_set([]) == []


class _RecordingFetchClient:
    """Answers every UID FETCH from the requested set, recording each command's uid-set arg."""

    def __init__(self) -> None:
        self.uid_sets: list[str] = []

    def uid(self, command: str, uid_set: str, items: str) -> tuple[str, list[object]]:
        self.uid_sets.append(uid_set)
        uids = _expand_uid_set(uid_set)
        if "RFC822.SIZE" in items:
            return ("OK", [f"1 (UID {uid} RFC822.SIZE {uid + 7})".encode() for uid in uids])
        return ("OK", [(f"1 (UID {uid} BODY[] {{3}})".encode(), b"raw") for uid in uids])


async def test_fetch_sizes_chunks_a_large_scattered_mailbox_and_merges_results() -> None:
    # One comma-joined FETCH of thousands of UIDs exceeds server command-line limits (Dovecot
    # replies BAD at 64KB), imaplib raises, and the cursor never advances — so the session must
    # issue bounded chunks and merge their results.
    client = _RecordingFetchClient()
    uids = list(range(1_000_001, 1_000_001 + 2 * 2500, 2))  # 2500 scattered (incompressible) UIDs
    executor = ThreadPoolExecutor(max_workers=1)
    session = DefaultImapFetchSession(client, executor)  # type: ignore[arg-type]

    try:
        sizes = await session.fetch_sizes(uids)
    finally:
        executor.shutdown(wait=False)

    assert len(client.uid_sets) > 1  # chunked, not one unbounded command
    assert all(len(uid_set) <= _MAX_UID_SET_CHARS for uid_set in client.uid_sets)
    assert sizes == {uid: uid + 7 for uid in uids}  # every chunk's sizes merged


async def test_fetch_messages_chunks_a_large_scattered_mailbox_and_merges_results() -> None:
    # The body-fetch path comma-joined its UID list identically — same bound, results concatenated.
    client = _RecordingFetchClient()
    uids = list(range(1_000_001, 1_000_001 + 2 * 2500, 2))
    executor = ThreadPoolExecutor(max_workers=1)
    session = DefaultImapFetchSession(client, executor)  # type: ignore[arg-type]

    try:
        messages = await session.fetch_messages(uids)
    finally:
        executor.shutdown(wait=False)

    assert len(client.uid_sets) > 1
    assert all(len(uid_set) <= _MAX_UID_SET_CHARS for uid_set in client.uid_sets)
    assert [message.uid for message in messages] == uids


async def test_fetch_sizes_contiguous_first_sync_compresses_to_one_range_command() -> None:
    # A contiguous 10k-UID first sync must become a single tiny `1:10000` atom, not 10k numbers.
    client = _RecordingFetchClient()
    executor = ThreadPoolExecutor(max_workers=1)
    session = DefaultImapFetchSession(client, executor)  # type: ignore[arg-type]

    try:
        sizes = await session.fetch_sizes(list(range(1, 10_001)))
    finally:
        executor.shutdown(wait=False)

    assert client.uid_sets == ["1:10000"]
    assert len(sizes) == 10_000


# ── LIST responses carrying the mailbox name as a literal (Dovecot tuple form) ──
class _LiteralListClient:
    """LIST data shaped like Dovecot's: a quote-bearing folder name arrives as a literal tuple."""

    def list(self) -> tuple[str, list[object]]:
        return (
            "OK",
            [
                b'(\\HasNoChildren) "/" "INBOX"',
                (b'(\\HasNoChildren) "/" {13}', b'Projects "Q1"'),
                b"",  # imaplib emits the (empty) rest-of-line after a literal as its own item
                (b'(\\Noselect) "/" {6}', b"Hidden"),
            ],
        )


async def test_list_folders_parses_literal_mailbox_names_to_the_true_name() -> None:
    # A LIST name with quotes/8-bit bytes arrives as a (prefix, literal) TUPLE; taking item[0]
    # captured b'{13}' as the folder name, so the real folder was silently never synced.
    executor = ThreadPoolExecutor(max_workers=1)
    session = DefaultImapFetchSession(_LiteralListClient(), executor)  # type: ignore[arg-type]

    try:
        folders = await session.list_folders()
    finally:
        executor.shutdown(wait=False)

    assert folders == ["INBOX", 'Projects "Q1"']


class _SelectRecordingClient:
    """Records the exact mailbox argument SELECT receives; returns a valid UIDVALIDITY."""

    def __init__(self) -> None:
        self.selected: object | None = None

    def select(self, mailbox: object, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.selected = mailbox
        return ("OK", [b"1"])

    def response(self, code: str) -> tuple[str, list[bytes]]:
        return (code, [b"77"])


async def test_select_folder_sends_eight_bit_literal_name_as_raw_bytes() -> None:
    # An 8-bit (raw-UTF-8 literal) folder name passed as str hits imaplib's hardwired ASCII
    # command encoding (UnicodeEncodeError, caught by no IMAP handler) and fails the WHOLE
    # sync; sent as latin-1 bytes it round-trips the server's exact name and the folder syncs.
    client = _SelectRecordingClient()
    executor = ThreadPoolExecutor(max_workers=1)
    session = DefaultImapFetchSession(client, executor)  # type: ignore[arg-type]
    folder = "Entwürfe".encode().decode("latin-1")  # exactly as _parse_list_item yields it

    try:
        uidvalidity = await session.select_folder(folder)
    finally:
        executor.shutdown(wait=False)

    assert uidvalidity == 77
    assert client.selected == b'"Entw\xc3\xbcrfe"'


async def test_select_folder_uidvalidity_zero_maps_to_not_selectable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # RFC 3501 requires nonzero UIDVALIDITY but nonconforming servers (older Exchange/Courier)
    # emit 0. Returning 0 would flow into the cursor upsert and violate 0014's
    # ck_sync_cursor_uidvalidity_positive INSIDE the batch transaction — permanently failing the
    # whole connection's sync. 0 must map to the existing None = skip-this-folder contract, and
    # the skip must be LOUD (GPT cross-vendor review: a silently skipped SELECTABLE folder is
    # ongoing mail loss) — a WARNING naming the folder fires on every sync.
    class _ZeroUidvalidityClient:
        def select(self, mailbox: object, readonly: bool = False) -> tuple[str, list[bytes]]:
            return ("OK", [b"1"])

        def response(self, code: str) -> tuple[str, list[bytes]]:
            return (code, [b"0"])

    executor = ThreadPoolExecutor(max_workers=1)
    session = DefaultImapFetchSession(_ZeroUidvalidityClient(), executor)  # type: ignore[arg-type]

    try:
        with caplog.at_level("WARNING", logger="app.connectors.imap.fetch_session"):
            assert await session.select_folder("INBOX") is None
    finally:
        executor.shutdown(wait=False)

    assert any(
        "UIDVALIDITY" in record.message and "SKIPPED" in record.message
        for record in caplog.records
    )


async def test_select_folder_unencodable_name_skips_the_folder_not_the_sync() -> None:
    # Belt-and-braces: a name latin-1 cannot encode (codepoint > 255 — impossible from
    # _parse_list_item, possible from a future caller) skips THIS folder (None); the
    # UnicodeEncodeError must never escape to fail the whole connection's sync.
    class _NeverSelectedClient:
        def select(self, mailbox: object, readonly: bool = False) -> tuple[str, list[bytes]]:
            raise AssertionError("select must not be reached for an unencodable name")

    executor = ThreadPoolExecutor(max_workers=1)
    session = DefaultImapFetchSession(_NeverSelectedClient(), executor)  # type: ignore[arg-type]

    try:
        result = await session.select_folder("emoji-\U0001f600")
    finally:
        executor.shutdown(wait=False)

    assert result is None


# ── STARTTLS-or-refuse on the use_ssl=false fetch path (no cleartext LOGIN, ever) ──
class _PlainImap4NoTls:
    """A fake imaplib.IMAP4 whose server offers no STARTTLS; records lifecycle calls."""

    error = imaplib.IMAP4.error  # bound at import time, before any monkeypatching
    last: _PlainImap4NoTls | None = None

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.login_called = False
        self.shutdown_called = False
        type(self).last = self

    def starttls(self, ssl_context: ssl.SSLContext | None = None) -> None:
        raise type(self).error("STARTTLS extension not supported by server.")

    def login(self, username: str, secret: str) -> None:
        self.login_called = True

    def shutdown(self) -> None:
        self.shutdown_called = True

    def logout(self) -> None:
        pass


class _PlainImap4WithTls:
    """A fake imaplib.IMAP4 that accepts STARTTLS, recording the call order + ssl context."""

    error = imaplib.IMAP4.error
    last: _PlainImap4WithTls | None = None

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.calls: list[str] = []
        self.starttls_context: ssl.SSLContext | None = None
        type(self).last = self

    def starttls(self, ssl_context: ssl.SSLContext | None = None) -> None:
        self.starttls_context = ssl_context
        self.calls.append("starttls")

    def login(self, username: str, secret: str) -> None:
        self.calls.append("login")

    def logout(self) -> None:
        self.calls.append("logout")


async def test_open_imap_session_plain_without_starttls_refuses_cleartext_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(imaplib, "IMAP4", _PlainImap4NoTls)

    with pytest.raises(ImapTlsUnavailableError):
        await open_imap_session(_PLAIN_PARAMS, "super-secret-pw")

    fake = _PlainImap4NoTls.last
    assert fake is not None
    assert fake.login_called is False  # the password never travelled over cleartext
    assert fake.shutdown_called is True  # the refused socket was closed, not leaked


async def test_open_imap_session_plain_upgrades_via_starttls_before_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(imaplib, "IMAP4", _PlainImap4WithTls)

    session = await open_imap_session(_PLAIN_PARAMS, "pw")
    await session.close()

    fake = _PlainImap4WithTls.last
    assert fake is not None
    assert fake.calls[:2] == ["starttls", "login"]  # TLS is up BEFORE credentials travel
    assert isinstance(fake.starttls_context, ssl.SSLContext)
    assert fake.starttls_context.verify_mode == ssl.CERT_REQUIRED
