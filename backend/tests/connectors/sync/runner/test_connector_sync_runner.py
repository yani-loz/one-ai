"""
Role: Integration tests for ConnectorSyncRunner — the background sync orchestration end to end on
      the real tenant engine (RLS-enforced): it stores new mail + advances the cursor + finalizes,
      RESUMES from a stored cursor, correctly handles a UIDVALIDITY RESET (regression: never advance
      the cursor past re-scanned mail), ABORTS when the claim is stolen mid-run (the fence), holds
      NO open DB transaction across count_pending's network enumeration (idle-in-transaction
      hygiene), writes the sync.finished audit row on BOTH the success and the crashed-run finalize
      (report H-5 — content-blind: status/counts/duration, never mail content), and — the
      NON-NEGOTIABLE — never touches another org's connection.
Used by: pytest (tests/connectors/sync/runner). Real migrated DB via the runner conftest.
Depends on: ConnectorSyncRunner + the connection/cursor/run repositories + the conftest fakes +
            the identity AuditLog model (read-back asserts).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base.incremental_fetch import FetchBatch, FolderCursor
from app.connectors.imap.models.email import EmailMessage
from app.connectors.models.connector_connection import ConnectorConnection
from app.connectors.models.connector_sync_run import ConnectorSyncRun
from app.connectors.repositories.connector_connection_repository import (
    ConnectorConnectionRepository,
)
from app.connectors.repositories.connector_sync_cursor_repository import (
    ConnectorSyncCursorRepository,
)
from app.connectors.repositories.connector_sync_run_repository import ConnectorSyncRunRepository
from app.connectors.security.credential_cipher import CredentialCipher
from app.connectors.sync.connector_sync_runner import ConnectorSyncRunner
from app.identity.models.audit_log import AuditLog
from tests.connectors.sync.runner.conftest import (
    FakeIncrementalConnector,
    folder_batch,
    make_registry,
    message,
    seed_connection,
)


async def _col(session: AsyncSession, connection_id: UUID, column: object) -> object:
    """Read one connection column straight from the DB (no ORM identity-map staleness)."""
    return (
        await session.execute(select(column).where(ConnectorConnection.id == connection_id))
    ).scalar_one()


async def _email_count(session: AsyncSession, org_id: UUID) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(EmailMessage).where(EmailMessage.org_id == org_id)
        )
    ).scalar_one()


async def _claim(session: AsyncSession, org_id: UUID, connection_id: UUID, run_id: UUID) -> None:
    """Claim the connection + open the ledger row, as SyncService does before spawning."""
    await ConnectorConnectionRepository(session).claim_for_sync(
        org_id, connection_id, run_id, stale_seconds=300
    )
    await ConnectorSyncRunRepository(session).start_run(org_id, connection_id, run_id)


def _runner(cipher: CredentialCipher, connector: FakeIncrementalConnector) -> ConnectorSyncRunner:
    """Build a runner over the fake connector; the heartbeat is slow enough to never fire."""
    return ConnectorSyncRunner(cipher, make_registry(connector), heartbeat_seconds=3600)


async def _sync_finished_rows(session: AsyncSession, org_id: UUID) -> list[AuditLog]:
    """Read back one org's sync.finished audit rows (org-scoped: the shared table accumulates)."""
    result = await session.execute(
        select(AuditLog).where(AuditLog.org_id == org_id, AuditLog.action == "sync.finished")
    )
    return list(result.scalars().all())


async def test_run_stores_new_mail_advances_cursor_and_finalizes(
    db_session: AsyncSession, sync_cipher: CredentialCipher
) -> None:
    org = uuid4()
    connection = await seed_connection(db_session, org, sync_cipher)
    run_id = uuid4()
    await _claim(db_session, org, connection.id, run_id)
    await db_session.commit()
    connector = FakeIncrementalConnector(
        [folder_batch("INBOX", 100, message(1, "a@x"), message(2, "b@x"), message(3, "c@x"))]
    )

    await _runner(sync_cipher, connector).run(org, connection.id, run_id)

    assert await _email_count(db_session, org) == 3
    cursor = (
        await ConnectorSyncCursorRepository(db_session).list_for_connection(org, connection.id)
    )["INBOX"]
    assert (cursor.uidvalidity, cursor.last_seen_uid) == (100, 3)
    assert await _col(db_session, connection.id, ConnectorConnection.sync_status) == "idle"
    assert await _col(db_session, connection.id, ConnectorConnection.synced_count) == 3
    ledger_status = (
        await db_session.execute(
            select(ConnectorSyncRun.status).where(ConnectorSyncRun.run_id == run_id)
        )
    ).scalar_one()
    assert ledger_status == "succeeded"


async def test_run_resumes_from_the_stored_cursor(
    db_session: AsyncSession, sync_cipher: CredentialCipher
) -> None:
    org = uuid4()
    connection = await seed_connection(db_session, org, sync_cipher)
    await ConnectorSyncCursorRepository(db_session).upsert(
        org, connection.id, "INBOX", uidvalidity=100, last_seen_uid=5, failed_uids=[]
    )
    run_id = uuid4()
    await _claim(db_session, org, connection.id, run_id)
    await db_session.commit()
    connector = FakeIncrementalConnector([folder_batch("INBOX", 100, message(6, "f@x"))])

    await _runner(sync_cipher, connector).run(org, connection.id, run_id)

    # The runner passed the stored resume point to the fetcher (last_seen_uid=5, same generation).
    assert connector.fetch_cursors[0]["INBOX"].last_seen_uid == 5
    assert connector.fetch_cursors[0]["INBOX"].uidvalidity == 100
    cursor = (
        await ConnectorSyncCursorRepository(db_session).list_for_connection(org, connection.id)
    )["INBOX"]
    assert cursor.last_seen_uid == 6  # advanced past the one new message


async def test_run_uidvalidity_reset_advances_to_the_real_high_water_not_the_old_floor(
    db_session: AsyncSession, sync_cipher: CredentialCipher
) -> None:
    # Regression: a server UIDVALIDITY reset re-scans from UID 1. The cursor must land on the NEW
    # generation's real high-water (3), NOT the stale pre-reset floor (1000) — else mail is lost.
    org = uuid4()
    connection = await seed_connection(db_session, org, sync_cipher)
    await ConnectorSyncCursorRepository(db_session).upsert(
        org, connection.id, "INBOX", uidvalidity=111, last_seen_uid=1000, failed_uids=[]
    )
    run_id = uuid4()
    await _claim(db_session, org, connection.id, run_id)
    await db_session.commit()
    connector = FakeIncrementalConnector(  # NEW uidvalidity 222 → fetcher re-scanned from UID 1
        [folder_batch("INBOX", 222, message(1, "a@x"), message(2, "b@x"), message(3, "c@x"))]
    )

    await _runner(sync_cipher, connector).run(org, connection.id, run_id)

    cursor = (
        await ConnectorSyncCursorRepository(db_session).list_for_connection(org, connection.id)
    )["INBOX"]
    assert cursor.uidvalidity == 222
    assert cursor.last_seen_uid == 3  # the bug would leave this at 1000
    assert await _email_count(db_session, org) == 3  # the re-scanned mail is stored


async def test_run_does_not_advance_past_a_requested_but_unreturned_uid(
    db_session: AsyncSession, sync_cipher: CredentialCipher
) -> None:
    # Regression (never-lose-mail): the fetcher requested [1,2,3] but the server returned only 1
    # and 3 (a partial/dropped FETCH of the still-existing UID 2). The cursor must STOP at 1, NOT
    # jump to 3, so UID 2 is re-fetched next run instead of being silently skipped forever.
    org = uuid4()
    connection = await seed_connection(db_session, org, sync_cipher)
    run_id = uuid4()
    await _claim(db_session, org, connection.id, run_id)
    await db_session.commit()
    connector = FakeIncrementalConnector(
        [folder_batch("INBOX", 100, message(1, "a@x"), message(3, "c@x"), requested_uids=[1, 2, 3])]
    )

    await _runner(sync_cipher, connector).run(org, connection.id, run_id)

    cursor = (
        await ConnectorSyncCursorRepository(db_session).list_for_connection(org, connection.id)
    )["INBOX"]
    assert cursor.last_seen_uid == 1  # stopped at the gap (UID 2), did NOT advance to 3
    assert await _email_count(db_session, org) == 2  # 1 and 3 are stored; 2 retried next run


async def test_run_aborts_when_the_claim_is_held_by_another_run(
    db_session: AsyncSession, sync_cipher: CredentialCipher
) -> None:
    org = uuid4()
    connection = await seed_connection(db_session, org, sync_cipher)
    other_run = uuid4()
    await _claim(db_session, org, connection.id, other_run)  # the claim belongs to another run
    await db_session.commit()
    connector = FakeIncrementalConnector([folder_batch("INBOX", 100, message(1, "a@x"))])

    await _runner(sync_cipher, connector).run(
        org, connection.id, uuid4()
    )  # a run that never owned it

    assert await _email_count(db_session, org) == 0  # first fenced write fails → nothing stored
    assert await _col(db_session, connection.id, ConnectorConnection.sync_run_id) == other_run
    assert await _col(db_session, connection.id, ConnectorConnection.sync_status) == "running"
    # A LOST fence finalizes nothing — and therefore audits nothing (the owner logs its own end).
    assert await _sync_finished_rows(db_session, org) == []


async def test_run_stops_when_disabled_mid_sync(
    db_session: AsyncSession, sync_cipher: CredentialCipher
) -> None:
    # Audit TC-IM-B02: disabling a connection mid-sync stops the in-flight run at the next batch
    # boundary — no further mail ingests, and the run finalizes cleanly (not stuck 'running').
    org = uuid4()
    connection = await seed_connection(db_session, org, sync_cipher)
    run_id = uuid4()
    await _claim(db_session, org, connection.id, run_id)
    await db_session.execute(  # an admin disabled it after the claim, before the batch loop
        update(ConnectorConnection)
        .where(ConnectorConnection.id == connection.id)
        .values(disabled_at=datetime.now(UTC))
    )
    await db_session.commit()
    connector = FakeIncrementalConnector([folder_batch("INBOX", 100, message(1, "a@x"))])

    await _runner(sync_cipher, connector).run(org, connection.id, run_id)

    assert await _email_count(db_session, org) == 0  # broke before ingesting the batch
    assert await _col(db_session, connection.id, ConnectorConnection.sync_status) == "idle"


class _CountPendingProbeConnector(FakeIncrementalConnector):
    """A fake that records whether the runner's session is mid-transaction in count_pending."""

    def __init__(self, batches: list[FetchBatch], sessions: list[AsyncSession]) -> None:
        super().__init__(batches)
        self._sessions = sessions
        self.in_transaction_during_count: bool | None = None

    async def count_pending(self, cursors: Mapping[str, FolderCursor]) -> int:
        # sessions[0] is the runner's own session (run() opens it before the heartbeat task).
        self.in_transaction_during_count = self._sessions[0].in_transaction()
        return await super().count_pending(cursors)


async def test_run_holds_no_db_transaction_during_count_pending(
    db_session: AsyncSession, sync_cipher: CredentialCipher, monkeypatch: pytest.MonkeyPatch
) -> None:
    # count_pending enumerates IMAP folders over the network (potentially minutes); an open
    # transaction across it sits idle-in-transaction and is killed on managed PG. The runner
    # must commit its read transaction FIRST. The scoped_session wrapper below only CAPTURES
    # the sessions the runner opens (delegating to the real one) so the probe can inspect them.
    import app.connectors.sync.connector_sync_runner as runner_module

    captured_sessions: list[AsyncSession] = []
    real_scoped_session = runner_module.scoped_session

    @asynccontextmanager
    async def _capturing_scoped_session(org_id: UUID) -> AsyncIterator[AsyncSession]:
        async with real_scoped_session(org_id) as session:
            captured_sessions.append(session)
            yield session

    monkeypatch.setattr(runner_module, "scoped_session", _capturing_scoped_session)
    org = uuid4()
    connection = await seed_connection(db_session, org, sync_cipher)
    run_id = uuid4()
    await _claim(db_session, org, connection.id, run_id)
    await db_session.commit()
    connector = _CountPendingProbeConnector(
        [folder_batch("INBOX", 100, message(1, "a@x"))], captured_sessions
    )

    await _runner(sync_cipher, connector).run(org, connection.id, run_id)

    assert connector.in_transaction_during_count is False  # released before the enumeration
    assert await _email_count(db_session, org) == 1  # and the sync still completed normally
    assert await _col(db_session, connection.id, ConnectorConnection.sync_status) == "idle"


async def test_run_ignores_another_orgs_connection(
    db_session: AsyncSession, sync_cipher: CredentialCipher
) -> None:
    # Cross-tenant non-negotiable: a runner invoked for org_b must not see/sync org_a's connection
    # (RLS makes the row invisible to the org_b-scoped session → get_in_org returns None → no-op).
    org_a, org_b = uuid4(), uuid4()
    connection = await seed_connection(db_session, org_a, sync_cipher)
    run_id = uuid4()
    await _claim(db_session, org_a, connection.id, run_id)
    await db_session.commit()
    connector = FakeIncrementalConnector([folder_batch("INBOX", 100, message(1, "a@x"))])

    await _runner(sync_cipher, connector).run(org_b, connection.id, run_id)  # WRONG org

    assert await _email_count(db_session, org_a) == 0  # nothing ingested for either org
    assert await _col(db_session, connection.id, ConnectorConnection.sync_status) == "running"


async def test_run_success_writes_content_blind_sync_finished_audit_row(
    db_session: AsyncSession, sync_cipher: CredentialCipher
) -> None:
    # H-5: a finished run leaves a sync.finished row committed WITH the finalize — counts +
    # status + duration only; NOTHING from the mail (sender/subject/body) enters the trail.
    org = uuid4()
    connection = await seed_connection(db_session, org, sync_cipher)
    run_id = uuid4()
    await _claim(db_session, org, connection.id, run_id)
    await db_session.commit()
    connector = FakeIncrementalConnector(
        [folder_batch("INBOX", 100, message(1, "a@x"), message(2, "b@x"))]
    )

    await _runner(sync_cipher, connector).run(org, connection.id, run_id)

    rows = await _sync_finished_rows(db_session, org)
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_type == "system"  # background task — no human actor
    assert row.entity_type == "connector_connection"
    assert row.entity_id == connection.id
    assert row.details["run_id"] == str(run_id)
    assert row.details["status"] == "succeeded"
    assert row.details["messages_seen"] == 2
    assert row.details["messages_stored"] == 2
    assert row.details["messages_skipped"] == 0
    assert row.details["messages_failed"] == 0
    assert row.details["duration_seconds"] >= 0
    serialized = json.dumps(row.details)
    assert "boyan@globex.com" not in serialized  # the sender never enters the audit trail
    assert "Hello." not in serialized  # nor the body
    assert "Subject" not in serialized  # nor any header content


async def test_run_crash_still_writes_failed_sync_finished_audit_row(
    db_session: AsyncSession, sync_cipher: CredentialCipher
) -> None:
    # H-5: a run that CRASHES (here: the credential fails to decrypt) finalizes on a FRESH
    # session via _finalize_failure — the failed sync.finished audit row must still land.
    org = uuid4()
    connection = await seed_connection(db_session, org, sync_cipher)
    run_id = uuid4()
    await _claim(db_session, org, connection.id, run_id)
    await db_session.commit()
    wrong_cipher = CredentialCipher(
        "another-key-so-the-decrypt-must-fail-here", require_secure=False
    )
    connector = FakeIncrementalConnector([folder_batch("INBOX", 100, message(1, "a@x"))])

    await _runner(wrong_cipher, connector).run(org, connection.id, run_id)

    ledger_status = (
        await db_session.execute(
            select(ConnectorSyncRun.status).where(ConnectorSyncRun.run_id == run_id)
        )
    ).scalar_one()
    assert ledger_status == "failed"
    rows = await _sync_finished_rows(db_session, org)
    assert len(rows) == 1
    assert rows[0].details["status"] == "failed"
    assert rows[0].details["messages_stored"] == 0
    assert rows[0].details["run_id"] == str(run_id)
