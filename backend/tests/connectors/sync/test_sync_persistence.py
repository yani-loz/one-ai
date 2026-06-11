"""
Role: Tests for the SyncRunner persistence layer — the atomic single-runner CLAIM (win/block,
      stale-reclaim, disabled-guard, org scope), the FENCED progress writes (a stolen claim's writes
      become no-ops), the cursor UPSERT/list, and the run ledger (start/finalize/abandon).
Used by: pytest (tests/connectors/sync). Real DB via the sync conftest.
Depends on: the three connector repositories + the sync conftest.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.models.connector_connection import ConnectorConnection
from app.connectors.models.connector_sync_run import ConnectorSyncRun
from app.connectors.repositories.connector_connection_repository import (
    ConnectorConnectionRepository,
)
from app.connectors.repositories.connector_sync_cursor_repository import (
    ConnectorSyncCursorRepository,
)
from app.connectors.repositories.connector_sync_run_repository import ConnectorSyncRunRepository
from tests.connectors.sync.conftest import seed_connection


async def _col(session: AsyncSession, connection_id: UUID, column: object) -> object:
    """Read one column for a connection straight from the DB (no ORM identity-map staleness)."""
    return (
        await session.execute(select(column).where(ConnectorConnection.id == connection_id))
    ).scalar_one()


async def _age_heartbeat(session: AsyncSession, connection_id: UUID, minutes: int) -> None:
    await session.execute(
        update(ConnectorConnection)
        .where(ConnectorConnection.id == connection_id)
        .values(sync_heartbeat_at=datetime.now(UTC) - timedelta(minutes=minutes))
    )


async def test_claim_succeeds_then_blocks_a_fresh_second_runner(db_session: AsyncSession) -> None:
    org = uuid4()
    connection = await seed_connection(db_session, org)
    repo = ConnectorConnectionRepository(db_session)
    run_a, run_b = uuid4(), uuid4()

    assert await repo.claim_for_sync(org, connection.id, run_a, stale_seconds=300) is True
    assert await repo.claim_for_sync(org, connection.id, run_b, stale_seconds=300) is False

    assert await _col(db_session, connection.id, ConnectorConnection.sync_status) == "running"
    assert await _col(db_session, connection.id, ConnectorConnection.sync_run_id) == run_a


async def test_stale_claim_can_be_reclaimed(db_session: AsyncSession) -> None:
    org = uuid4()
    connection = await seed_connection(db_session, org)
    repo = ConnectorConnectionRepository(db_session)
    run_a, run_c = uuid4(), uuid4()
    await repo.claim_for_sync(org, connection.id, run_a, stale_seconds=300)
    await _age_heartbeat(db_session, connection.id, minutes=10)  # past the 5-min window

    assert await repo.claim_for_sync(org, connection.id, run_c, stale_seconds=300) is True
    assert await _col(db_session, connection.id, ConnectorConnection.sync_run_id) == run_c


async def test_fenced_writes_fail_after_the_claim_is_stolen(db_session: AsyncSession) -> None:
    org = uuid4()
    connection = await seed_connection(db_session, org)
    repo = ConnectorConnectionRepository(db_session)
    run_a, run_c = uuid4(), uuid4()
    await repo.claim_for_sync(org, connection.id, run_a, stale_seconds=300)
    await _age_heartbeat(db_session, connection.id, minutes=10)
    await repo.claim_for_sync(org, connection.id, run_c, stale_seconds=300)  # run_c steals it

    assert await repo.heartbeat(org, connection.id, run_a) is False  # the old run lost the fence
    assert await repo.set_synced_count(org, connection.id, run_a, 5) is False
    assert await repo.heartbeat(org, connection.id, run_c) is True  # the new run still owns it


async def test_finalize_sync_idle_stamps_last_synced(db_session: AsyncSession) -> None:
    org = uuid4()
    connection = await seed_connection(db_session, org)
    repo = ConnectorConnectionRepository(db_session)
    run = uuid4()
    await repo.claim_for_sync(org, connection.id, run, stale_seconds=300)

    assert await repo.finalize_sync(org, connection.id, run, status="idle", error=None) is True
    assert await _col(db_session, connection.id, ConnectorConnection.sync_status) == "idle"
    assert await _col(db_session, connection.id, ConnectorConnection.last_synced_at) is not None


async def test_disabled_connection_cannot_be_claimed(db_session: AsyncSession) -> None:
    org = uuid4()
    connection = await seed_connection(db_session, org, disabled=True)
    repo = ConnectorConnectionRepository(db_session)

    assert await repo.claim_for_sync(org, connection.id, uuid4(), stale_seconds=300) is False


async def test_claim_is_org_scoped(db_session: AsyncSession) -> None:
    # Cross-tenant: another org cannot claim this org's connection (no claim, no leak).
    org_a, org_b = uuid4(), uuid4()
    connection = await seed_connection(db_session, org_a)
    repo = ConnectorConnectionRepository(db_session)

    assert await repo.claim_for_sync(org_b, connection.id, uuid4(), stale_seconds=300) is False
    assert await _col(db_session, connection.id, ConnectorConnection.sync_status) == "idle"


async def test_cursor_upsert_inserts_then_updates(db_session: AsyncSession) -> None:
    org = uuid4()
    connection = await seed_connection(db_session, org)
    repo = ConnectorSyncCursorRepository(db_session)

    await repo.upsert(org, connection.id, "INBOX", uidvalidity=10, last_seen_uid=5, failed_uids=[3])
    first = (await repo.list_for_connection(org, connection.id))["INBOX"]
    assert (first.uidvalidity, first.last_seen_uid, first.failed_uids) == (10, 5, [3])

    await repo.upsert(
        org, connection.id, "INBOX", uidvalidity=10, last_seen_uid=9, failed_uids=[3, 7]
    )
    second = (await repo.list_for_connection(org, connection.id))["INBOX"]
    assert (second.last_seen_uid, second.failed_uids) == (9, [3, 7])  # updated, not duplicated


async def test_run_ledger_start_finalize_and_abandon(db_session: AsyncSession) -> None:
    org = uuid4()
    connection = await seed_connection(db_session, org)
    repo = ConnectorSyncRunRepository(db_session)
    run_a = uuid4()
    await repo.start_run(org, connection.id, run_a)
    await repo.finalize(
        org, run_a, status="succeeded", seen=10, stored=8, skipped=2, failed=0, error=None
    )

    # A new run starts while a leftover one is still 'running' → abandon the stale one.
    run_b, run_c = uuid4(), uuid4()
    await repo.start_run(org, connection.id, run_b)
    await repo.start_run(org, connection.id, run_c)
    await repo.mark_stale_running_abandoned(org, connection.id, keep_run_id=run_c)

    rows = (
        await db_session.execute(
            select(ConnectorSyncRun.run_id, ConnectorSyncRun.status).where(
                ConnectorSyncRun.org_id == org
            )
        )
    ).all()
    status_by_run = {row.run_id: row.status for row in rows}
    assert status_by_run[run_a] == "succeeded"
    assert status_by_run[run_b] == "abandoned"  # the stale running run was reconciled
    assert status_by_run[run_c] == "running"  # the keeper is untouched
