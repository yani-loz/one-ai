"""
Role: Company-side connector orchestration — store a connection (credential encrypted), list /
      get / delete it, and TEST it (connect + authenticate). Holds the business logic (rule A5).
Used by: routes.connector_routes; constructed in connectors.dependencies on the caller's
         TENANT-scoped session.
Depends on: ConnectorConnectionRepository, CredentialCipher, ConnectorRegistry, the connector
            enums/exceptions/model, the base ConnectionCheck.
Key invariants:
  - CROSS-TENANT ISOLATION (non-negotiable): every method takes org_id (from the verified JWT)
    and loads via get_in_org(id, org_id) — another org's connection resolves to
    ConnectionNotFoundError (-> 404), never a 403/200-empty leak.
  - SECRET HANDLING: the password is encrypted on create (never stored/logged in plaintext) and
    decrypted only transiently inside a connection test; it never enters `config`, `last_error`,
    a response, or a log.
  - FAILURE ISOLATION: test_connection wraps the decrypt + registry.create + verify in a single
    guard — ANY unexpected error becomes ConnectionCheck(ok=False), so a broken/removed connector
    or an unreadable credential reports the connection as `error` (HTTP 200), never a 500. Expected
    failures (bad auth / unreachable) come back as the connector's own ConnectionCheck message.
  - Duplicate (org, type, username) is rejected up-front with DuplicateConnectionError (-> 409);
    the unique constraint is the backstop.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.connectors.base.connector import ConnectionCheck
from app.connectors.base.registry import ConnectorRegistry
from app.connectors.enums import AuthMethod, ConnectionStatus, ConnectorType
from app.connectors.exceptions import (
    ConnectionNotFoundError,
    DuplicateConnectionError,
)
from app.connectors.models.connector_connection import ConnectorConnection
from app.connectors.repositories.connector_connection_repository import (
    ConnectorConnectionRepository,
)
from app.connectors.schemas.connector_schemas import CreateConnectionRequest
from app.connectors.security.credential_cipher import CredentialCipher

logger = logging.getLogger(__name__)


class ConnectorService:
    """Configure + verify an org's connector connections (point 1: IMAP connect/auth)."""

    def __init__(
        self,
        connections: ConnectorConnectionRepository,
        cipher: CredentialCipher,
        registry: ConnectorRegistry,
    ) -> None:
        """Wire the repository, credential cipher, and connector registry."""
        self._connections = connections
        self._cipher = cipher
        self._registry = registry

    async def create_connection(
        self, org_id: UUID, request: CreateConnectionRequest
    ) -> ConnectorConnection:
        """Store a new connection with its credential encrypted at rest.

        Raises:
            DuplicateConnectionError: this org already has a connection for
                (connector_type, username) (-> 409) — caught both up-front and on the insert race.
        """
        if await self._connections.exists(org_id, request.connector_type.value, request.username):
            raise DuplicateConnectionError("A connection for this mailbox already exists.")
        connection = ConnectorConnection(
            org_id=org_id,
            connector_type=request.connector_type.value,
            display_name=request.display_name,
            auth_method=AuthMethod.app_password.value,
            username=request.username,
            config={"host": request.host, "port": request.port, "use_ssl": request.use_ssl},
            secret_ciphertext=self._cipher.encrypt(request.password),
            secret_key_version=self._cipher.key_version,
            status=ConnectionStatus.configured.value,
        )
        try:
            return await self._connections.insert(connection)
        except IntegrityError as exc:
            # Two admins created the same mailbox concurrently: both passed the exists() pre-check,
            # one lost on uq_connector_connection_identity. Translate to the documented 409 rather
            # than letting an uncaught IntegrityError surface as a 500.
            raise DuplicateConnectionError("A connection for this mailbox already exists.") from exc

    async def list_connections(self, org_id: UUID) -> list[ConnectorConnection]:
        """Return all of the caller's org connections, newest-first."""
        return await self._connections.list_for_org(org_id)

    async def get_connection(self, org_id: UUID, connection_id: UUID) -> ConnectorConnection:
        """Load one of the caller's connections, or raise ConnectionNotFoundError (-> 404)."""
        connection = await self._connections.get_in_org(connection_id, org_id)
        if connection is None:
            raise ConnectionNotFoundError("Connection not found.")
        return connection

    async def delete_connection(self, org_id: UUID, connection_id: UUID) -> None:
        """Delete one of the caller's connections (404 if it isn't theirs)."""
        connection = await self.get_connection(org_id, connection_id)
        await self._connections.delete(connection)

    async def test_connection(self, org_id: UUID, connection_id: UUID) -> ConnectorConnection:
        """Verify a stored connection and persist the outcome (status/last_checked_at/last_error).

        A failed verification is a SUCCESSFUL test reporting a negative result: the row's status
        becomes 'error' with a sanitized last_error, and the (updated) row is returned. Raises
        only ConnectionNotFoundError (-> 404) when the connection isn't the caller's.
        """
        connection = await self.get_connection(org_id, connection_id)
        check = await self._verify(connection)
        connection.status = (
            ConnectionStatus.connected.value if check.ok else ConnectionStatus.error.value
        )
        connection.last_checked_at = datetime.now(UTC)
        connection.last_error = None if check.ok else check.message
        return connection

    async def _verify(self, connection: ConnectorConnection) -> ConnectionCheck:
        """Decrypt + build the connector + verify, isolating any failure as ok=False."""
        try:
            connector_type = ConnectorType(connection.connector_type)
            secret = self._cipher.decrypt(connection.secret_ciphertext)
            config = {**(connection.config or {}), "username": connection.username}
            connector = self._registry.create(connector_type, config, secret)
            return await connector.verify_connection()
        except Exception:  # belt-and-suspenders: no connector failure becomes a 500
            logger.exception("Connection test failed unexpectedly for connection %s", connection.id)
            return ConnectionCheck(ok=False, message="The connection test failed unexpectedly.")
