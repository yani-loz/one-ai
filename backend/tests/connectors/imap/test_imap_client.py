"""
Role: Unit tests for DefaultImapClient.verify_login + upgrade_to_starttls — the credential-bearing
      verify path must VERIFY server certificates (default ssl context on IMAP4_SSL, never
      imaplib's unverified default) and must never LOGIN over cleartext: a non-SSL connection is
      upgraded via STARTTLS or refused with ImapTlsUnavailableError.
Used by: pytest (tests/connectors/imap). No real IMAP server — imaplib is monkeypatched at the
         vendor boundary (fake IMAP4 / IMAP4_SSL classes).
Depends on: app.connectors.imap.client, app.connectors.imap.config.
"""

from __future__ import annotations

import imaplib
import ssl

import pytest

from app.connectors.imap.client import (
    DefaultImapClient,
    ImapAuthError,
    ImapConnectionError,
    ImapTlsUnavailableError,
    upgrade_to_starttls,
)
from app.connectors.imap.config import imap_params_from_config

_SSL_PARAMS = imap_params_from_config(
    {"host": "mail.example.com", "port": 993, "use_ssl": True, "username": "u@x.com"}
)
_PLAIN_PARAMS = imap_params_from_config(
    {"host": "mail.example.com", "port": 143, "use_ssl": False, "username": "u@x.com"}
)


class _FakeImap4Ssl:
    """A fake imaplib.IMAP4_SSL capturing the ssl_context it was constructed with."""

    error = imaplib.IMAP4.error  # bound at import time, before any monkeypatching
    last: _FakeImap4Ssl | None = None

    def __init__(
        self,
        host: str,
        port: int,
        ssl_context: ssl.SSLContext | None = None,
        timeout: float | None = None,
    ) -> None:
        self.ssl_context = ssl_context
        self.calls: list[str] = []
        type(self).last = self

    def login(self, username: str, secret: str) -> None:
        self.calls.append("login")

    def noop(self) -> None:
        self.calls.append("noop")

    def logout(self) -> None:
        self.calls.append("logout")


class _RejectingLoginImap4Ssl(_FakeImap4Ssl):
    """An IMAP4_SSL fake whose LOGIN is rejected with the vendor error (bad credentials)."""

    def login(self, username: str, secret: str) -> None:
        raise type(self).error("LOGIN failed")


class _UnreachableImap4Ssl:
    """An IMAP4_SSL fake whose construction fails like a dead host (socket error)."""

    error = imaplib.IMAP4.error

    def __init__(
        self,
        host: str,
        port: int,
        ssl_context: ssl.SSLContext | None = None,
        timeout: float | None = None,
    ) -> None:
        raise OSError("connection refused")


class _PlainImap4NoTls:
    """A fake imaplib.IMAP4 whose server offers no STARTTLS; records lifecycle calls."""

    error = imaplib.IMAP4.error
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

    def noop(self) -> None:
        self.calls.append("noop")

    def logout(self) -> None:
        self.calls.append("logout")


async def test_verify_login_ssl_passes_a_verifying_default_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The verify path used to construct IMAP4_SSL with NO ssl_context — an unverified context
    # that accepts any self-signed cert on the one path that ALWAYS carries the password.
    monkeypatch.setattr(imaplib, "IMAP4_SSL", _FakeImap4Ssl)

    await DefaultImapClient().verify_login(_SSL_PARAMS, "pw")

    fake = _FakeImap4Ssl.last
    assert fake is not None
    assert isinstance(fake.ssl_context, ssl.SSLContext)
    assert fake.ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert fake.ssl_context.check_hostname is True


async def test_verify_login_rejected_credentials_raises_auth_error_secret_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(imaplib, "IMAP4_SSL", _RejectingLoginImap4Ssl)

    with pytest.raises(ImapAuthError) as exc_info:
        await DefaultImapClient().verify_login(_SSL_PARAMS, "super-secret-pw")

    assert "super-secret-pw" not in str(exc_info.value)
    fake = _RejectingLoginImap4Ssl.last
    assert fake is not None
    assert "logout" in fake.calls  # the finally still logs the connection out


async def test_verify_login_unreachable_host_raises_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(imaplib, "IMAP4_SSL", _UnreachableImap4Ssl)

    with pytest.raises(ImapConnectionError):
        await DefaultImapClient().verify_login(_SSL_PARAMS, "pw")


async def test_verify_login_plain_without_starttls_refuses_cleartext_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(imaplib, "IMAP4", _PlainImap4NoTls)

    with pytest.raises(ImapTlsUnavailableError) as exc_info:
        await DefaultImapClient().verify_login(_PLAIN_PARAMS, "super-secret-pw")

    fake = _PlainImap4NoTls.last
    assert fake is not None
    assert fake.login_called is False  # the password never travelled over cleartext
    assert fake.shutdown_called is True  # the refused socket was closed, not leaked
    assert "super-secret-pw" not in str(exc_info.value)


async def test_verify_login_plain_upgrades_via_starttls_before_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(imaplib, "IMAP4", _PlainImap4WithTls)

    await DefaultImapClient().verify_login(_PLAIN_PARAMS, "pw")

    fake = _PlainImap4WithTls.last
    assert fake is not None
    assert fake.calls[:2] == ["starttls", "login"]  # TLS is up BEFORE credentials travel
    assert isinstance(fake.starttls_context, ssl.SSLContext)
    assert fake.starttls_context.verify_mode == ssl.CERT_REQUIRED


class _HandshakeFailClient:
    """A connected fake whose STARTTLS handshake fails at the socket layer (OSError)."""

    def __init__(self) -> None:
        self.shutdown_called = False

    def starttls(self, ssl_context: ssl.SSLContext | None = None) -> None:
        raise OSError("TLS handshake failed")

    def shutdown(self) -> None:
        self.shutdown_called = True


def test_upgrade_to_starttls_handshake_failure_raises_connection_error_and_closes() -> None:
    client = _HandshakeFailClient()

    with pytest.raises(ImapConnectionError):
        upgrade_to_starttls(client)  # type: ignore[arg-type]  # duck-typed imaplib fake

    assert client.shutdown_called is True


def test_imap_tls_unavailable_error_is_a_connection_error() -> None:
    # Existing handlers catch ImapConnectionError (connector verify_connection, sync runner);
    # the refusal must map to a failed connection there, never an unhandled 500.
    assert issubclass(ImapTlsUnavailableError, ImapConnectionError)
