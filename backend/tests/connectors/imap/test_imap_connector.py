"""Unit tests for ImapConnector — verify_connection outcomes via a fake client (no real server)."""

from __future__ import annotations

from app.connectors.enums import ConnectorType
from app.connectors.imap.client import ImapAuthError, ImapConnectionError
from app.connectors.imap.config import ImapConnectionParams
from app.connectors.imap.connector import ImapConnector, build_imap_connector

_PARAMS = ImapConnectionParams(host="mail.example.com", port=993, use_ssl=True, username="u@x")


class _FakeClient:
    """An ImapClient stub that either succeeds or raises a pre-set error on verify_login."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    async def verify_login(self, params: ImapConnectionParams, secret: str) -> None:
        if self._error is not None:
            raise self._error


async def test_verify_connection_success_returns_ok() -> None:
    connector = ImapConnector(_PARAMS, "pw", client=_FakeClient())

    check = await connector.verify_connection()

    assert check.ok is True


async def test_verify_connection_auth_error_returns_not_ok() -> None:
    connector = ImapConnector(_PARAMS, "pw", client=_FakeClient(ImapAuthError("x")))

    check = await connector.verify_connection()

    assert check.ok is False
    assert "uthentication" in check.message


async def test_verify_connection_connection_error_returns_not_ok() -> None:
    connector = ImapConnector(_PARAMS, "pw", client=_FakeClient(ImapConnectionError("x")))

    check = await connector.verify_connection()

    assert check.ok is False
    assert "reach" in check.message


async def test_verify_connection_message_never_contains_the_secret() -> None:
    connector = ImapConnector(_PARAMS, "super-secret-pw", client=_FakeClient(ImapAuthError("x")))

    check = await connector.verify_connection()

    assert "super-secret-pw" not in check.message


def test_connector_type_is_imap() -> None:
    connector = ImapConnector(_PARAMS, "pw", client=_FakeClient())

    assert connector.connector_type is ConnectorType.imap


def test_build_imap_connector_returns_an_imap_connector() -> None:
    connector = build_imap_connector(
        {"host": "h", "port": 993, "use_ssl": True, "username": "u@x"}, "s"
    )

    assert connector.connector_type is ConnectorType.imap
