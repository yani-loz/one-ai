"""
Role: Tests for SyncService — the trigger/observe orchestration: start_sync CLAIMS + opens the
      ledger + commits + spawns (the claim is committed before the spawn), rejects a disabled or
      already-running connection, and — the NON-NEGOTIABLE — neither start_sync nor get_sync_status
      ever acts on another org's connection (cross-org → 404).
Used by: pytest (tests/connectors/sync). Real DB via the sync conftest (3-table schema).
Depends on: SyncService + the connection/run repositories + the sync conftest. The spawn seam is
            faked so the background runner never actually runs in a service unit test.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any
from uuid import uuid4

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
from tests.connectors.sync.conftest import seed_connection


class _CapturingSpawn:
    """A spawn seam: records the label + CLOSES the coroutine (the runner never runs in-test)."""

    def __init__(self) -> None:
        self.labels: list[str] = []

    def __call__(self, coro: Coroutine[Any, Any, None], *, label: str) -> asyncio.Task[None] | None:
        self.labels.append(label)
        coro.close()  # we assert the spawn happened; we don't drive the runner here
        return None


def _service(session: AsyncSession, spawn: _CapturingSpawn) -> SyncService:
    """Build a SyncService whose spawned runner is inert (cipher/registry never exercised)."""
    cipher = CredentialCipher("svc-test-key-not-secure-but-long-enough", require_secure=False)
    runner = ConnectorSyncRunner(cipher, build_default_registry())
    return SyncService(
        session=session,
        connections=ConnectorConnectionRepository(session),
        runs=ConnectorSyncRunRepository(session),
        runner=runner,
        spawn=spawn,
    )


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
