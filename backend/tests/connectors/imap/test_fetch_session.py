"""
Role: Unit tests for open_imap_session's failure handling — a client that connected before a LOGIN
      failure must be CLOSED (no leaked socket on a bad-credential retry); the public error stays
      secret-free. The imaplib client is injected (the vendor boundary).
Used by: pytest (tests/connectors/imap). No real IMAP server.
Depends on: app.connectors.imap.fetch_session, app.connectors.imap.client (the two vendor errors).
"""

from __future__ import annotations

import imaplib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from app.connectors.imap.client import ImapAuthError, ImapConnectionError
from app.connectors.imap.config import imap_params_from_config
from app.connectors.imap.fetch_session import (
    DefaultImapFetchSession,
    _imap_quote,
    _parse_internaldate,
    open_imap_session,
)

_PARAMS = imap_params_from_config(
    {"host": "mail.example.com", "port": 993, "use_ssl": True, "username": "u@x.com"}
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
