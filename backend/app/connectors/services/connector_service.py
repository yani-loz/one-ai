"""
Role: Company-side connector orchestration — store a connection (credential encrypted), list /
      get / delete it, and TEST it (connect + authenticate). Holds the business logic (rule A5).
Used by: routes.connector_routes; constructed in connectors.dependencies on the caller's
         TENANT-scoped session.
Depends on: ConnectorConnectionRepository, CredentialCipher, ConnectorRegistry, the connector
            enums/exceptions/model, the base ConnectionCheck, identity.services.audit_service
            (AuditService — the connector.* lifecycle trail) + identity.Principal/AuditActorType.
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
  - POOL HYGIENE: test_connection ENDS the request transaction (commit) before the up-to-15s
    network verify, so the pooled DB connection is returned instead of pinned idle-in-transaction
    for the verify's duration (concurrent tests would otherwise exhaust the pool). The status
    write afterwards autobegins a fresh transaction, committed by the request unit-of-work.
  - Duplicate (org, type, username) is rejected up-front with DuplicateConnectionError (-> 409);
    the unique constraint is the backstop. ONLY a uq_connector_connection_identity violation maps
    to 409 — any other IntegrityError (e.g. the 0014 org-root FK) propagates as the bug it is,
    never masquerading as a duplicate.
  - AUDITED LIFECYCLE (report H-5): create/disable/enable/delete record a connector.* audit row
    on the SAME session (committed atomically with the change by the request unit-of-work),
    carrying the ACTOR identity (the verified Principal). Events are CONTENT-BLIND: ids +
    connector_type + host only — never the credential, never the mailbox username/email content.
    Idempotent no-ops (re-disable / re-enable) emit no row, mirroring the identity domain.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_object_session

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
from app.identity.enums import AuditActorType
from app.identity.principal import Principal
from app.identity.services.audit_service import AuditAction, AuditEvent, AuditService

logger = logging.getLogger(__name__)

_ENTITY_CONNECTION = "connector_connection"
_IDENTITY_CONSTRAINT = "uq_connector_connection_identity"


def _violates_connection_identity(exc: IntegrityError) -> bool:
    """True iff `exc` is the uq_connector_connection_identity unique violation.

    Prefers the driver's structured constraint name (asyncpg chains the original
    UniqueViolationError as exc.orig.__cause__ with .constraint_name); falls back to
    matching the constraint name in the database error message.
    """
    cause = getattr(exc.orig, "__cause__", None)
    constraint_name = getattr(cause, "constraint_name", None)
    if constraint_name is not None:
        return constraint_name == _IDENTITY_CONSTRAINT
    return _IDENTITY_CONSTRAINT in str(exc.orig)


class ConnectorService:
    """Configure + verify an org's connector connections (point 1: IMAP connect/auth)."""

    def __init__(
        self,
        connections: ConnectorConnectionRepository,
        cipher: CredentialCipher,
        registry: ConnectorRegistry,
        audit: AuditService,
    ) -> None:
        """Wire the repository, credential cipher, connector registry, and audit writer."""
        self._connections = connections
        self._cipher = cipher
        self._registry = registry
        self._audit = audit

    async def create_connection(
        self, org_id: UUID, request: CreateConnectionRequest, actor: Principal
    ) -> ConnectorConnection:
        """Store a new connection with its credential encrypted at rest, audited same-tx.

        Records a `connector.created` audit row on the same session (commits atomically with
        the insert). Content-blind: connector_type + host only — never the credential, never
        the mailbox username.

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
            created = await self._connections.insert(connection)
        except IntegrityError as exc:
            # Two admins created the same mailbox concurrently: both passed the exists() pre-check,
            # one lost on uq_connector_connection_identity. Translate ONLY that violation to the
            # documented 409 — any other integrity failure (e.g. the 0014 org-root FK) is a real
            # bug and must propagate, not be misreported as a duplicate.
            if not _violates_connection_identity(exc):
                raise
            raise DuplicateConnectionError("A connection for this mailbox already exists.") from exc
        await self._audit.record(
            self._connection_event(
                AuditAction.CONNECTOR_CREATED,
                actor,
                created,
                {"connector_type": created.connector_type, "host": request.host},
            )
        )
        return created

    async def list_connections(self, org_id: UUID) -> list[ConnectorConnection]:
        """Return all of the caller's org connections, newest-first."""
        return await self._connections.list_for_org(org_id)

    async def get_connection(self, org_id: UUID, connection_id: UUID) -> ConnectorConnection:
        """Load one of the caller's connections, or raise ConnectionNotFoundError (-> 404)."""
        connection = await self._connections.get_in_org(connection_id, org_id)
        if connection is None:
            raise ConnectionNotFoundError("Connection not found.")
        return connection

    async def delete_connection(
        self, org_id: UUID, connection_id: UUID, actor: Principal
    ) -> None:
        """Delete one of the caller's connections (404 if it isn't theirs), audited same-tx.

        The DELETE cascades the connection's ingested corpus away (codebase-review H-7), so the
        `connector.deleted` audit row — recorded on the same session, committed with the delete —
        is the only durable record the connection existed. Content-blind: type + host only.
        """
        connection = await self.get_connection(org_id, connection_id)
        # Build + stage the audit row BEFORE the delete: the instance's attributes are still
        # loaded, and both writes commit atomically with the request unit-of-work anyway.
        event = self._connection_event(
            AuditAction.CONNECTOR_DELETED,
            actor,
            connection,
            {
                "connector_type": connection.connector_type,
                "host": (connection.config or {}).get("host"),
            },
        )
        await self._audit.record(event)
        await self._connections.delete(connection)

    async def disable_connection(
        self, org_id: UUID, connection_id: UUID, actor: Principal
    ) -> ConnectorConnection:
        """Disable a connection (reversible) — stops sync + revokes AI access (design §8).

        Idempotent: re-disabling keeps the original disabled_at and emits NO duplicate audit row;
        an actual enable->disable transition records `connector.disabled` same-tx. Returns the
        updated row; raises ConnectionNotFoundError (-> 404) when the connection isn't the
        caller's.
        """
        connection = await self.get_connection(org_id, connection_id)
        if connection.disabled_at is None:
            connection.disabled_at = datetime.now(UTC)
            await self._audit.record(
                self._connection_event(AuditAction.CONNECTOR_DISABLED, actor, connection, {})
            )
        return connection

    async def enable_connection(
        self, org_id: UUID, connection_id: UUID, actor: Principal
    ) -> ConnectorConnection:
        """Re-enable a disabled connection (clears disabled_at). 404 if it isn't the caller's.

        Idempotent: enabling an already-active connection emits NO audit row; a real
        disable->enable transition records `connector.enabled` same-tx (restoring sync + AI
        access is as audit-worthy as removing it — mirrors user reactivate).
        """
        connection = await self.get_connection(org_id, connection_id)
        if connection.disabled_at is not None:
            connection.disabled_at = None
            await self._audit.record(
                self._connection_event(AuditAction.CONNECTOR_ENABLED, actor, connection, {})
            )
        return connection

    async def test_connection(self, org_id: UUID, connection_id: UUID) -> ConnectorConnection:
        """Verify a stored connection and persist the outcome (status/last_checked_at/last_error).

        A failed verification is a SUCCESSFUL test reporting a negative result: the row's status
        becomes 'error' with a sanitized last_error, and the (updated) row is returned. Raises
        only ConnectionNotFoundError (-> 404) when the connection isn't the caller's.

        Transaction shape (pool hygiene): the request transaction is COMMITTED after the read,
        BEFORE the up-to-15s network verify — otherwise every concurrent test pins one pooled DB
        connection idle-in-transaction for the verify's full duration and exhausts the pool. The
        row stays usable across the commit (expire_on_commit=False); the status mutation below
        autobegins a fresh transaction that the request unit-of-work commits.
        """
        connection = await self.get_connection(org_id, connection_id)
        # Release the DB connection back to the pool before the slow network verify. The session
        # is reached via the loaded row (a deliberate, narrow exception to "services hold no
        # transaction logic" — the route's unit-of-work still owns the final commit). Reads only
        # so far in this method, so the commit persists nothing unexpected.
        session = async_object_session(connection)
        if session is not None:
            await session.commit()
        check = await self._verify(connection)
        connection.status = (
            ConnectionStatus.connected.value if check.ok else ConnectionStatus.error.value
        )
        connection.last_checked_at = datetime.now(UTC)
        connection.last_error = None if check.ok else check.message
        return connection

    @staticmethod
    def _connection_event(
        action: str, actor: Principal, connection: ConnectorConnection, details: dict[str, object]
    ) -> AuditEvent:
        """Build a content-blind connector lifecycle audit event (ids/type/host — no secret/email).

        actor_type maps from the verified Principal's subject_type (the connector routes only
        admit company users today, but the mapping stays correct if a platform path appears).
        """
        return AuditEvent(
            action=action,
            actor_type=AuditActorType(actor.subject_type),
            actor_id=actor.subject_id,
            org_id=connection.org_id,
            entity_type=_ENTITY_CONNECTION,
            entity_id=connection.id,
            details=details,
        )

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
