"""
Role: Tests for SyncService — the trigger/observe orchestration: start_sync CLAIMS + opens the
      ledger + audits + commits + spawns (the claim is committed before the spawn), rejects a
      disabled or already-running connection, records sync.started with the triggering actor
      (or 'system' when none — report H-5), and — the NON-NEGOTIABLE — neither start_sync nor
      get_sync_status ever acts on another org's connection (cross-org → 404).
Used by: pytest (tests/connectors/sync). Real DB via the sync conftest (3-table schema).
Depends on: SyncService + the connection/run repositories + the sync conftest + the identity
            audit model/repo/service + Principal. The spawn seam is faked so the background
            runner never actually runs in a service unit test.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base.registry import build_default_registry
from app.connectors.exceptions import (
    ConnectionNotFoundError,
    ConnectorDisabledError,
    SyncAlreadyRunningError,
)
from app.connectors.models.connector_sync_run import ConnectorSyncRun
from app.connectors.repositories.connector_connection_repository import (
    ConnectorConnectionRepository,
)
from app.connectors.repositories.connector_sync_run_repository import ConnectorSyncRunRepository
from app.connectors.security.credential_cipher import CredentialCipher
from app.connectors.sync.connector_sync_runner import ConnectorSyncRunner
from app.connectors.sync.sync_service import SyncService
from app.identity.models.audit_log import AuditLog
from app.identity.principal import Principal
from app.identity.repositories.audit_repository import AuditRepository
from app.identity.services.audit_service import AuditService
from tests.connectors.sync.conftest import seed_connection


class _CapturingSpawn:
    """A spawn seam: records the label + CLOSES the coroutine (the runner never runs in-test)."""

    def __init__(self) -> None:
        self.labels: list[str] = []

    def __call__(self, coro: Coroutine[Any, Any, None], *, label: str) -> asyncio.Task[None] | None:
        self.labels.append(label)
        coro.close()  # we assert the spawn happened; we don't drive the runner here
        return None


class _AlwaysEntitled:
    """Stub entitlement reader (always entitled) — the Tier-1 sync ceiling is exercised in the
    route/CO-01 tests; these service-unit tests focus on the claim/ledger/audit logic."""

    async def is_entitled(self, _org_id: UUID, _connector_type: str) -> bool:
        return True


def _service(session: AsyncSession, spawn: _CapturingSpawn) -> SyncService:
    """Build a SyncService whose spawned runner is inert (cipher/registry never exercised)."""
    cipher = CredentialCipher("svc-test-key-not-secure-but-long-enough", require_secure=False)
    runner = ConnectorSyncRunner(cipher, build_default_registry())
    return SyncService(
        session=session,
        connections=ConnectorConnectionRepository(session),
        runs=ConnectorSyncRunRepository(session),
        runner=runner,
        audit=AuditService(AuditRepository(session)),
        entitlements=_AlwaysEntitled(),  # type: ignore[arg-type]  # test stub, only is_entitled used
        spawn=spawn,
    )


def _actor(org_id: UUID) -> Principal:
    """The company_admin triggering the sync (subject_id lands on the sync.started row)."""
    return Principal(subject_id=uuid4(), org_id=org_id, role="company_admin", subject_type="user")


async def _audit_rows(session: AsyncSession, org_id: UUID, action: str) -> list[AuditLog]:
    """Read back one org's audit rows for `action` (org-scoped: the shared table accumulates)."""
    result = await session.execute(
        select(AuditLog)
        .where(AuditLog.org_id == org_id, AuditLog.action == action)
        .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
    )
    return list(result.scalars().all())


async def test_start_sync_claims_opens_ledger_and_spawns(db_session: AsyncSession) -> None:
    org = uuid4()
    connection = await seed_connection(db_session, org)
    spawn = _CapturingSpawn()

    result = await _service(db_session, spawn).start_sync(org, connection.id)

    assert result.sync_status == "running"
    assert result.sync_run_id is not None
    assert spawn.labels == [f"sync:{connection.id}"]
    ledger_status = (
        await db_session.execute(
            select(ConnectorSyncRun.status).where(ConnectorSyncRun.run_id == result.sync_run_id)
        )
    ).scalar_one()
    assert ledger_status == "running"


async def test_start_sync_rejects_a_disabled_connection(db_session: AsyncSession) -> None:
    org = uuid4()
    connection = await seed_connection(db_session, org, disabled=True)
    spawn = _CapturingSpawn()

    with pytest.raises(ConnectorDisabledError):
        await _service(db_session, spawn).start_sync(org, connection.id)

    assert spawn.labels == []  # never spawned a runner for a disabled connection


async def test_start_sync_rejects_when_a_sync_is_already_running(db_session: AsyncSession) -> None:
    org = uuid4()
    connection = await seed_connection(db_session, org)
    # A fresh live claim already holds the slot (heartbeat not stale → not reclaimable).
    await ConnectorConnectionRepository(db_session).claim_for_sync(
        org, connection.id, uuid4(), stale_seconds=300
    )
    spawn = _CapturingSpawn()

    with pytest.raises(SyncAlreadyRunningError):
        await _service(db_session, spawn).start_sync(org, connection.id)

    assert spawn.labels == []


async def test_start_sync_cross_tenant_returns_not_found(db_session: AsyncSession) -> None:
    # NON-NEGOTIABLE: org_b triggering a sync on org_a's connection resolves to 404, never a leak.
    org_a, org_b = uuid4(), uuid4()
    connection = await seed_connection(db_session, org_a)
    spawn = _CapturingSpawn()

    with pytest.raises(ConnectionNotFoundError):
        await _service(db_session, spawn).start_sync(org_b, connection.id)

    assert spawn.labels == []


async def test_start_sync_writes_sync_started_audit_row_with_actor(
    db_session: AsyncSession,
) -> None:
    # H-5: a claimed run is never unlogged — sync.started rides the claim commit, carrying the
    # triggering principal and the run id (ids only — content-blind by construction).
    org = uuid4()
    connection = await seed_connection(db_session, org)
    actor = _actor(org)

    result = await _service(db_session, _CapturingSpawn()).start_sync(
        org, connection.id, actor=actor
    )

    rows = await _audit_rows(db_session, org, "sync.started")
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_type == "user"
    assert row.actor_id == actor.subject_id
    assert row.entity_type == "connector_connection"
    assert row.entity_id == connection.id
    assert row.details == {"run_id": str(result.sync_run_id)}


async def test_start_sync_without_actor_records_system_actor(db_session: AsyncSession) -> None:
    # A scheduled/background trigger has no principal — the row must still land, as 'system'.
    org = uuid4()
    connection = await seed_connection(db_session, org)

    await _service(db_session, _CapturingSpawn()).start_sync(org, connection.id)

    rows = await _audit_rows(db_session, org, "sync.started")
    assert len(rows) == 1
    assert rows[0].actor_type == "system"
    assert rows[0].actor_id is None


async def test_start_sync_rejected_disabled_writes_no_audit_row(db_session: AsyncSession) -> None:
    # A rejected trigger is not a started sync — no phantom sync.started row may appear.
    org = uuid4()
    connection = await seed_connection(db_session, org, disabled=True)

    with pytest.raises(ConnectorDisabledError):
        await _service(db_session, _CapturingSpawn()).start_sync(org, connection.id, _actor(org))

    assert await _audit_rows(db_session, org, "sync.started") == []


async def test_get_sync_status_returns_the_progress_row(db_session: AsyncSession) -> None:
    org = uuid4()
    connection = await seed_connection(db_session, org)

    result = await _service(db_session, _CapturingSpawn()).get_sync_status(org, connection.id)

    assert result.id == connection.id
    assert result.sync_status == "idle"
    assert result.synced_count == 0


async def test_get_sync_status_cross_tenant_returns_not_found(db_session: AsyncSession) -> None:
    # NON-NEGOTIABLE: org_b reading org_a's sync status resolves to 404, never a cross-org peek.
    org_a, org_b = uuid4(), uuid4()
    connection = await seed_connection(db_session, org_a)

    with pytest.raises(ConnectionNotFoundError):
        await _service(db_session, _CapturingSpawn()).get_sync_status(org_b, connection.id)
