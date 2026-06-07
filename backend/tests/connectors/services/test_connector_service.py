"""
Role: Service-layer tests for ConnectorService — cross-tenant isolation (the testing.md
      non-negotiable at the service layer) and the FAILURE-ISOLATION invariant (no connector
      error becomes a 500: an unexpected raise or an unregistered/removed connector surfaces as
      status='error', never an exception out of test_connection).
Used by: pytest (tests/connectors/services).
Depends on: the connectors conftest (connector_schema + db_session), the real CredentialCipher,
            a controllable ConnectorRegistry, the service + repo + model + exceptions.
Key invariants tested:
  - get_connection / test_connection / delete_connection on another org's id -> 404.
  - test_connection isolates a connector that RAISES (unexpected) and a connector type that is
    NOT registered (removed/broken) -> status='error' + a generic message, no exception escapes.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base.connector import BaseConnector, ConnectionCheck
from app.connectors.base.registry import ConnectorRegistry
from app.connectors.enums import ConnectorType
from app.connectors.exceptions import ConnectionNotFoundError, DuplicateConnectionError
from app.connectors.repositories.connector_connection_repository import (
    ConnectorConnectionRepository,
)
from app.connectors.schemas.connector_schemas import CreateConnectionRequest
from app.connectors.security.credential_cipher import CredentialCipher
from app.connectors.services.connector_service import ConnectorService

_TEST_KEY = "a-strong-enough-test-key-0123456789abcd"


def _request(username: str = "sales@example.com") -> CreateConnectionRequest:
    return CreateConnectionRequest(
        connector_type=ConnectorType.imap,
        display_name="Sales mailbox",
        host="mail.example.com",
        port=993,
        use_ssl=True,
        username=username,
        password="imap-app-pw-123",
    )


def _service(session: AsyncSession, registry: ConnectorRegistry) -> ConnectorService:
    return ConnectorService(
        connections=ConnectorConnectionRepository(session),
        cipher=CredentialCipher(_TEST_KEY, require_secure=False),
        registry=registry,
    )


class _RaisingConnector(BaseConnector):
    """A connector whose verify_connection raises an UNEXPECTED error."""

    @property
    def connector_type(self) -> ConnectorType:
        return ConnectorType.imap

    async def verify_connection(self) -> ConnectionCheck:
        raise RuntimeError("unexpected connector failure")


async def test_get_connection_other_org_raises_not_found(db_session: AsyncSession) -> None:
    service = _service(db_session, ConnectorRegistry())
    org_a, org_b = uuid4(), uuid4()
    connection = await service.create_connection(org_a, _request())

    with pytest.raises(ConnectionNotFoundError):
        await service.get_connection(org_b, connection.id)


async def test_test_connection_other_org_raises_not_found(db_session: AsyncSession) -> None:
    service = _service(db_session, ConnectorRegistry())
    org_a, org_b = uuid4(), uuid4()
    connection = await service.create_connection(org_a, _request())

    with pytest.raises(ConnectionNotFoundError):
        await service.test_connection(org_b, connection.id)


async def test_delete_connection_other_org_raises_not_found(db_session: AsyncSession) -> None:
    service = _service(db_session, ConnectorRegistry())
    org_a, org_b = uuid4(), uuid4()
    connection = await service.create_connection(org_a, _request())

    with pytest.raises(ConnectionNotFoundError):
        await service.delete_connection(org_b, connection.id)


async def test_test_connection_isolates_unexpected_connector_error(
    db_session: AsyncSession,
) -> None:
    registry = ConnectorRegistry()
    registry.register(ConnectorType.imap, lambda config, secret: _RaisingConnector())
    service = _service(db_session, registry)
    org = uuid4()
    connection = await service.create_connection(org, _request())

    result = await service.test_connection(org, connection.id)

    assert result.status == "error"
    assert result.last_error == "The connection test failed unexpectedly."


async def test_create_connection_duplicate_insert_race_raises_duplicate(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The create RACE: two admins both pass the exists() pre-check, then the second insert loses on
    # uq_connector_connection_identity. We force that path by stubbing only the pre-check to miss;
    # the IntegrityError is REAL (a genuine same-tx unique violation against the first row).
    # The service must translate it to DuplicateConnectionError (-> 409), never let a 500 escape.
    service = _service(db_session, ConnectorRegistry())
    org = uuid4()
    await service.create_connection(org, _request(username="dup@example.com"))

    async def _exists_miss(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(service._connections, "exists", _exists_miss)

    with pytest.raises(DuplicateConnectionError):
        await service.create_connection(org, _request(username="dup@example.com"))
    await db_session.rollback()  # the IntegrityError aborted the tx — clean it for fixture teardown


async def test_test_connection_isolates_unregistered_connector(
    db_session: AsyncSession,
) -> None:
    # Empty registry == the imap connector was removed/failed to load. test must still not raise.
    service = _service(db_session, ConnectorRegistry())
    org = uuid4()
    connection = await service.create_connection(org, _request())

    result = await service.test_connection(org, connection.id)

    assert result.status == "error"
    assert result.last_error == "The connection test failed unexpectedly."
