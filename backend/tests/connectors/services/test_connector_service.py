"""
Role: Service-layer tests for ConnectorService — cross-tenant isolation (the testing.md
      non-negotiable at the service layer), the FAILURE-ISOLATION invariant (no connector
      error becomes a 500: an unexpected raise or an unregistered/removed connector surfaces as
      status='error', never an exception out of test_connection), and the connector.* audit
      trail (report H-5: every lifecycle mutation records WHO/WHAT, content-blind).
Used by: pytest (tests/connectors/services).
Depends on: the connectors conftest (connector_schema + db_session), the real CredentialCipher,
            a controllable ConnectorRegistry, the service + repo + model + exceptions, the
            identity audit model/repo/service + Principal.
Key invariants tested:
  - get_connection / test_connection / delete_connection on another org's id -> 404.
  - test_connection isolates a connector that RAISES (unexpected) and a connector type that is
    NOT registered (removed/broken) -> status='error' + a generic message, no exception escapes.
  - POOL HYGIENE: test_connection holds NO open DB transaction during the (slow, networked)
    verify — the pooled connection is released first, and the outcome still persists after.
  - AUDIT (H-5): create/disable/enable/delete each write one connector.* row carrying the actor
    + entity ids; details NEVER contain the credential or the mailbox username; idempotent
    re-disable writes no duplicate row.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
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
from app.identity.models.audit_log import AuditLog
from app.identity.principal import Principal
from app.identity.repositories.audit_repository import AuditRepository
from app.identity.services.audit_service import AuditService
from tests.conftest import seed_org

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


def _actor(org_id: UUID) -> Principal:
    """The company_admin performing the action (subject_id lands on the audit row)."""
    return Principal(subject_id=uuid4(), org_id=org_id, role="company_admin", subject_type="user")


class _AlwaysEntitled:
    """Stub entitlement reader (always entitled) — the Tier-1 ceiling is exercised end-to-end in
    the route/CO-01 tests; these service-unit tests focus on the create/verify/audit logic."""

    async def is_entitled(self, _org_id: UUID, _connector_type: str) -> bool:
        return True


def _service(session: AsyncSession, registry: ConnectorRegistry) -> ConnectorService:
    return ConnectorService(
        connections=ConnectorConnectionRepository(session),
        cipher=CredentialCipher(_TEST_KEY, require_secure=False),
        registry=registry,
        audit=AuditService(AuditRepository(session)),
        entitlements=_AlwaysEntitled(),  # type: ignore[arg-type]  # test stub, only is_entitled used
    )


async def _audit_rows(session: AsyncSession, org_id: UUID, action: str) -> list[AuditLog]:
    """Read back one org's audit rows for `action` (org-scoped: the shared table accumulates)."""
    result = await session.execute(
        select(AuditLog)
        .where(AuditLog.org_id == org_id, AuditLog.action == action)
        .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
    )
    return list(result.scalars().all())


class _RaisingConnector(BaseConnector):
    """A connector whose verify_connection raises an UNEXPECTED error."""

    @property
    def connector_type(self) -> ConnectorType:
        return ConnectorType.imap

    async def verify_connection(self) -> ConnectionCheck:
        raise RuntimeError("unexpected connector failure")


async def test_get_connection_other_org_raises_not_found(db_session: AsyncSession) -> None:
    service = _service(db_session, ConnectorRegistry())
    org_a, org_b = await seed_org(), await seed_org()
    connection = await service.create_connection(org_a, _request(), _actor(org_a))

    with pytest.raises(ConnectionNotFoundError):
        await service.get_connection(org_b, connection.id)


async def test_test_connection_other_org_raises_not_found(db_session: AsyncSession) -> None:
    service = _service(db_session, ConnectorRegistry())
    org_a, org_b = await seed_org(), await seed_org()
    connection = await service.create_connection(org_a, _request(), _actor(org_a))

    with pytest.raises(ConnectionNotFoundError):
        await service.test_connection(org_b, connection.id)


async def test_delete_connection_other_org_raises_not_found(db_session: AsyncSession) -> None:
    service = _service(db_session, ConnectorRegistry())
    org_a, org_b = await seed_org(), await seed_org()
    connection = await service.create_connection(org_a, _request(), _actor(org_a))

    with pytest.raises(ConnectionNotFoundError):
        await service.delete_connection(org_b, connection.id, _actor(org_b))


async def test_disable_then_enable_toggles_disabled_at(db_session: AsyncSession) -> None:
    service = _service(db_session, ConnectorRegistry())
    org = await seed_org()
    connection = await service.create_connection(org, _request(), _actor(org))

    disabled = await service.disable_connection(org, connection.id, _actor(org))
    assert disabled.disabled_at is not None

    enabled = await service.enable_connection(org, connection.id, _actor(org))
    assert enabled.disabled_at is None


async def test_disable_connection_other_org_raises_not_found(db_session: AsyncSession) -> None:
    service = _service(db_session, ConnectorRegistry())
    org_a, org_b = await seed_org(), await seed_org()
    connection = await service.create_connection(org_a, _request(), _actor(org_a))

    with pytest.raises(ConnectionNotFoundError):
        await service.disable_connection(org_b, connection.id, _actor(org_b))


async def test_test_connection_isolates_unexpected_connector_error(
    db_session: AsyncSession,
) -> None:
    registry = ConnectorRegistry()
    registry.register(ConnectorType.imap, lambda config, secret: _RaisingConnector())
    service = _service(db_session, registry)
    org = await seed_org()
    connection = await service.create_connection(org, _request(), _actor(org))

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
    org = await seed_org()
    await service.create_connection(org, _request(username="dup@example.com"), _actor(org))

    async def _exists_miss(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(service._connections, "exists", _exists_miss)

    with pytest.raises(DuplicateConnectionError):
        await service.create_connection(org, _request(username="dup@example.com"), _actor(org))
    await db_session.rollback()  # the IntegrityError aborted the tx — clean it for fixture teardown


async def test_test_connection_isolates_unregistered_connector(
    db_session: AsyncSession,
) -> None:
    # Empty registry == the imap connector was removed/failed to load. test must still not raise.
    service = _service(db_session, ConnectorRegistry())
    org = await seed_org()
    connection = await service.create_connection(org, _request(), _actor(org))

    result = await service.test_connection(org, connection.id)

    assert result.status == "error"
    assert result.last_error == "The connection test failed unexpectedly."


class _TransactionProbeConnector(BaseConnector):
    """Records whether the service's DB session is mid-transaction when verify runs."""

    def __init__(self, session: AsyncSession, observed: dict[str, bool]) -> None:
        self._session = session
        self._observed = observed

    @property
    def connector_type(self) -> ConnectorType:
        return ConnectorType.imap

    async def verify_connection(self) -> ConnectionCheck:
        self._observed["in_transaction_during_verify"] = self._session.in_transaction()
        return ConnectionCheck(ok=True, message="Connection verified.")


async def test_test_connection_holds_no_db_transaction_during_verify(
    db_session: AsyncSession,
) -> None:
    # Pool hygiene: the request transaction must END before the up-to-15s network verify —
    # otherwise every concurrent test pins a pooled DB connection idle-in-transaction for the
    # verify's full duration. The probe connector inspects the session mid-verify.
    observed: dict[str, bool] = {}
    registry = ConnectorRegistry()
    registry.register(
        ConnectorType.imap,
        lambda config, secret: _TransactionProbeConnector(db_session, observed),
    )
    service = _service(db_session, registry)
    org = await seed_org()
    connection = await service.create_connection(org, _request(), _actor(org))

    result = await service.test_connection(org, connection.id)

    assert observed == {"in_transaction_during_verify": False}  # connection was released
    assert result.status == "connected"  # and the outcome still persists normally
    assert result.last_error is None


async def test_create_connection_writes_content_blind_audit_row(
    db_session: AsyncSession,
) -> None:
    # H-5: creating a connector must leave a who/what/when row — content-blind (ids/type/host
    # only): the credential and the mailbox username NEVER appear anywhere in the row.
    service = _service(db_session, ConnectorRegistry())
    org, actor = await seed_org(), _actor(uuid4())

    connection = await service.create_connection(org, _request(), actor)

    rows = await _audit_rows(db_session, org, "connector.created")
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_type == "user"
    assert row.actor_id == actor.subject_id
    assert row.entity_type == "connector_connection"
    assert row.entity_id == connection.id
    assert row.details == {"connector_type": "imap", "host": "mail.example.com"}
    serialized = json.dumps(row.details)
    assert "imap-app-pw-123" not in serialized  # never the credential
    assert "sales@example.com" not in serialized  # never the mailbox username


async def test_disable_connection_writes_audit_row(db_session: AsyncSession) -> None:
    service = _service(db_session, ConnectorRegistry())
    org, actor = await seed_org(), _actor(uuid4())
    connection = await service.create_connection(org, _request(), actor)

    await service.disable_connection(org, connection.id, actor)

    rows = await _audit_rows(db_session, org, "connector.disabled")
    assert len(rows) == 1
    assert rows[0].actor_id == actor.subject_id
    assert rows[0].entity_id == connection.id


async def test_redisable_already_disabled_connection_emits_no_second_audit_row(
    db_session: AsyncSession,
) -> None:
    # Idempotent no-op: re-disabling must not inflate the trail with a phantom second action.
    service = _service(db_session, ConnectorRegistry())
    org, actor = await seed_org(), _actor(uuid4())
    connection = await service.create_connection(org, _request(), actor)
    await service.disable_connection(org, connection.id, actor)

    await service.disable_connection(org, connection.id, actor)

    assert len(await _audit_rows(db_session, org, "connector.disabled")) == 1


async def test_enable_connection_writes_audit_row(db_session: AsyncSession) -> None:
    # Restoring sync + AI access is as audit-worthy as removing it (mirrors user.reactivate).
    service = _service(db_session, ConnectorRegistry())
    org, actor = await seed_org(), _actor(uuid4())
    connection = await service.create_connection(org, _request(), actor)
    await service.disable_connection(org, connection.id, actor)

    await service.enable_connection(org, connection.id, actor)

    rows = await _audit_rows(db_session, org, "connector.enabled")
    assert len(rows) == 1
    assert rows[0].actor_id == actor.subject_id
    assert rows[0].entity_id == connection.id


async def test_delete_connection_writes_content_blind_audit_row(
    db_session: AsyncSession,
) -> None:
    # H-7/H-5: the DELETE cascades the corpus away — this row is the ONLY record it existed,
    # so it must carry actor + type/host, and still never the credential or the username.
    service = _service(db_session, ConnectorRegistry())
    org, actor = await seed_org(), _actor(uuid4())
    connection = await service.create_connection(org, _request(), actor)

    await service.delete_connection(org, connection.id, actor)

    rows = await _audit_rows(db_session, org, "connector.deleted")
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_id == actor.subject_id
    assert row.entity_id == connection.id
    assert row.details == {"connector_type": "imap", "host": "mail.example.com"}
    serialized = json.dumps(row.details)
    assert "imap-app-pw-123" not in serialized
    assert "sales@example.com" not in serialized
