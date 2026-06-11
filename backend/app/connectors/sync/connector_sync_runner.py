"""
Role: ConnectorSyncRunner — the background task that performs ONE sync. On its OWN tenant
      scoped_session (RLS-enforced) it builds the connector, streams new mail via the incremental
      fetcher, ingests each message (savepoint-isolated), advances the per-folder cursor riding each
      per-batch commit, keeps a fenced heartbeat on a second session, and finalizes. Never raises.
Used by: SyncService.start_sync (spawns run() as a background task AFTER committing the claim +
         the run-ledger row).
Depends on: core.database.scoped_session, the connection/cursor/run repositories, the email ingest
            service, the incremental-fetch contract + planner, the credential cipher + registry,
            identity.services.audit_service (AuditService — the sync.finished trail) +
            identity.enums.AuditActorType.
Key invariants:
  - SESSION/RLS: opens its OWN scoped_session(org_id) (NOT a request session); the after_begin
    listener re-binds app.current_org_id on every per-batch transaction. org/connection/run ids are
    PLAIN UUIDs — never a detached ORM row carried across a commit.
  - FENCED: every progress/heartbeat/finalize write is conditional on sync_run_id; the first one
    that returns False means a newer (reclaimed) run stole the claim → this run ABORTS and does NOT
    finalize, leaving the row to its rightful owner.
  - RESUMABLE + NEVER-LOSE-MAIL: a folder's cursor UPSERT rides the SAME commit as that batch's
    emails, so it can never be ahead of stored mail; a crash resumes from the cursor (re-fetch is
    idempotent on dedup_key). The cursor advances ONLY over durably STORED/SKIPPED UIDs and STOPS at
    the first that failed or was not returned — so a failure is re-fetched, never silently skipped.
    A PERMANENTLY-failing (poison) UID therefore STALLS that folder's cursor by design: this is LOUD
    (the UID is surfaced in the cursor's failed_uids) and non-destructive (new mail above it still
    stores via dedup); it must be drained operationally, never silently dropped.
  - PER-EMAIL ISOLATION: each ingest runs in a SAVEPOINT — a dedup collision or a poison email rolls
    back only that one email and the outer batch transaction stays usable.
  - AUDITED FINISH (report H-5): every WON finalize records a `sync.finished` audit row on the same
    session/commit as the finalize (status + counts + duration; actor_type='system' — a background
    task has no human actor). A crashed run's row still lands because _finalize_failure re-runs the
    finalize on a FRESH session. A LOST fence emits nothing — the rightful owner logs its own
    finish. Content-blind: ids/status/counts only — never the credential or any mail content.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base.incremental_fetch import (
    FetchBatch,
    FetchedMessage,
    FolderCursor,
    SupportsIncrementalFetch,
)
from app.connectors.base.registry import ConnectorRegistry
from app.connectors.enums import ConnectorType
from app.connectors.imap.services.email_ingest_service import EmailIngestService, IngestOutcome
from app.connectors.imap.sync.fetch_planner import highest_contiguous_uid
from app.connectors.models.connector_connection import ConnectorConnection
from app.connectors.repositories.connector_connection_repository import (
    ConnectorConnectionRepository,
)
from app.connectors.repositories.connector_sync_cursor_repository import (
    ConnectorSyncCursorRepository,
    FolderSyncState,
)
from app.connectors.repositories.connector_sync_run_repository import ConnectorSyncRunRepository
from app.connectors.security.credential_cipher import CredentialCipher
from app.core.database import scoped_session
from app.identity.enums import AuditActorType
from app.identity.repositories.audit_repository import AuditRepository
from app.identity.services.audit_service import AuditAction, AuditEvent, AuditService

logger = logging.getLogger(__name__)

_ENTITY_CONNECTION = "connector_connection"

HEARTBEAT_SECONDS = 30
# The claim's staleness window — must be >> HEARTBEAT_SECONDS and a single batch's wall-time.
STALE_SECONDS = 300
# Bound the per-folder failed_uids list persisted to the cursor. It is an OPERATOR SIGNAL only
# (which UIDs are currently stalling the folder); the cursor advance is driven by the accounted/gap
# logic, NOT by this set, so trimming it never affects correctness or loses mail.
MAX_FAILED_UIDS = 1000

# The UNIQUE(org_id, connection_id, dedup_key) constraint a benign re-ingest collides with.
_DEDUP_CONSTRAINT = "uq_email_message_dedup"


def _is_dedup_collision(error: IntegrityError) -> bool:
    """True iff this IntegrityError is the dedup-unique violation (a benign already-present row).

    Prefers the driver's structured `constraint_name` (asyncpg) and falls back to a constraint-name
    substring of the message; defaults to False so any OTHER constraint is treated as a real failure
    rather than a silent skip (upholds the never-lose-mail guarantee).
    """
    constraint = getattr(getattr(error, "orig", None), "constraint_name", None)
    if constraint is not None:
        return constraint == _DEDUP_CONSTRAINT
    return _DEDUP_CONSTRAINT in str(getattr(error, "orig", error))


@dataclass(slots=True)
class _Counts:
    """Running tally for one sync run (mirrors the ledger columns)."""

    seen: int = 0
    stored: int = 0
    skipped: int = 0
    failed: int = 0


@dataclass(slots=True)
class _FolderTracker:
    """Per-folder accounting for the resumable cursor advance within one run.

    `candidates` is the ascending list of REQUESTED UIDs (what the fetcher asked the server for,
    pruned past the floor each commit) — NOT the returned ones. `accounted` = UIDs that were durably
    STORED or SKIPPED (already present). highest_contiguous_uid advances only over accounted UIDs,
    STOPPING at the first that is not — so the cursor can NEVER step past a UID that was not durably
    stored. A requested UID the server didn't return, OR that failed to ingest, is therefore a gap
    re-fetched next run: a transient fault recovers; a permanent one visibly STALLS the folder (the
    cursor doesn't advance and new mail still flows via dedup), and NEVER silently drops mail.

    `failed` is the set of UIDs that failed to ingest — persisted to the cursor's failed_uids purely
    as an OPERATOR signal of what is stalling the folder. It is NOT load-bearing for the cursor (the
    accounted/gap logic is the source of truth), so capping it only trims the surfaced list, never
    the advance — eviction cannot lose mail.
    """

    uidvalidity: int
    floor: int
    candidates: list[int] = field(default_factory=list)
    accounted: set[int] = field(default_factory=set)
    failed: set[int] = field(default_factory=set)

    @classmethod
    def from_state(cls, state: FolderSyncState) -> _FolderTracker:
        """Seed from a stored cursor (floor = last UID; carry forward the failed signal)."""
        return cls(
            uidvalidity=state.uidvalidity or 0,
            floor=state.last_seen_uid,
            failed=set(state.failed_uids),
        )

    def note_requested(self, uids: list[int]) -> None:
        """Record the UIDs the fetcher REQUESTED this batch — the cursor-advance gap baseline."""
        self.candidates.extend(uids)

    def account(self, uid: int) -> None:
        """A durably STORED/SKIPPED UID — cursor may advance over it; clear any failed signal."""
        self.accounted.add(uid)
        self.failed.discard(uid)

    def fail(self, uid: int) -> None:
        """A FAILED ingest — NOT accounted; stays a gap (re-fetched next run); flag for ops."""
        self.failed.add(uid)


class ConnectorSyncRunner:
    """Runs one connection's incremental sync to completion as a background task."""

    def __init__(
        self,
        cipher: CredentialCipher,
        registry: ConnectorRegistry,
        *,
        heartbeat_seconds: int = HEARTBEAT_SECONDS,
    ) -> None:
        """Hold the credential cipher + connector registry; the heartbeat cadence is injectable."""
        self._cipher = cipher
        self._registry = registry
        self._heartbeat_seconds = heartbeat_seconds

    async def run(self, org_id: UUID, connection_id: UUID, run_id: UUID) -> None:
        """Background entry point — drive one sync to completion; NEVER raises (already 202'd)."""
        started_monotonic = time.monotonic()  # duration baseline for the sync.finished audit row
        try:
            async with scoped_session(org_id) as session:
                await self._sync(session, org_id, connection_id, run_id, started_monotonic)
        except Exception:
            logger.exception("Sync run %s (connection %s) failed", run_id, connection_id)
            await self._finalize_failure(org_id, connection_id, run_id, started_monotonic)

    async def _sync(
        self,
        session: AsyncSession,
        org_id: UUID,
        connection_id: UUID,
        run_id: UUID,
        started_monotonic: float,
    ) -> None:
        """Load the connection, stream + ingest every pending message, then finalize."""
        connections = ConnectorConnectionRepository(session)
        cursors = ConnectorSyncCursorRepository(session)
        runs = ConnectorSyncRunRepository(session)

        connection = await connections.get_in_org(connection_id, org_id)
        if connection is None:
            return  # deleted between the claim and the run — nothing to sync
        connector = self._build_connector(connection)
        if not isinstance(connector, SupportsIncrementalFetch):
            await self._finalize(
                session,
                connections,
                runs,
                org_id,
                connection_id,
                run_id,
                ok=False,
                error="This connector does not support sync.",
                counts=_Counts(),
                started_monotonic=started_monotonic,
            )
            return

        stored = await cursors.list_for_connection(org_id, connection_id)
        fetch_cursors = {
            folder: FolderCursor(state.uidvalidity, state.last_seen_uid)
            for folder, state in stored.items()
        }
        trackers = {folder: _FolderTracker.from_state(state) for folder, state in stored.items()}
        ingest = EmailIngestService(session, connection)
        counts = _Counts()

        ticker = asyncio.create_task(self._heartbeat_loop(org_id, connection_id, run_id))
        try:
            # count_pending enumerates IMAP folders over the network (potentially MINUTES). End
            # the read-only transaction the loads above opened FIRST, so no idle-in-transaction
            # connection is pinned across it (managed PG kills those via
            # idle_in_transaction_session_timeout). Reads only so far — nothing to lose; the
            # claim was committed by SyncService before this task spawned, and the fence stays
            # intact: set_total below autobegins a FRESH transaction (the after_begin listener
            # re-binds the tenant GUC) and is still conditional on sync_run_id.
            await session.commit()
            total = await connector.count_pending(fetch_cursors)
            if not await connections.set_total(org_id, connection_id, run_id, total):
                await session.rollback()  # claim already lost before we started
                return
            await session.commit()

            # aclosing: on an early return (stolen claim) or an exception, deterministically run
            # the generator's finally (logout + executor shutdown), not leaving it to async-gen GC.
            async with contextlib.aclosing(connector.fetch_incremental(fetch_cursors)) as stream:
                async for batch in stream:
                    # Stop cleanly if an admin disabled the connection mid-sync (audit TC-IM-B02):
                    # disable is NOT part of the sync_run_id fence, so re-check it each batch.
                    if await connections.is_disabled(org_id, connection_id):
                        break
                    kept = await self._ingest_batch(
                        session,
                        connections,
                        cursors,
                        ingest,
                        org_id,
                        connection_id,
                        run_id,
                        batch,
                        trackers,
                        counts,
                    )
                    if not kept:
                        await session.rollback()  # claim stolen — the new run owns the connection
                        return
        finally:
            await self._stop_ticker(ticker)

        await self._finalize(
            session,
            connections,
            runs,
            org_id,
            connection_id,
            run_id,
            ok=True,
            error=None,
            counts=counts,
            started_monotonic=started_monotonic,
        )

    async def _ingest_batch(
        self,
        session: AsyncSession,
        connections: ConnectorConnectionRepository,
        cursors: ConnectorSyncCursorRepository,
        ingest: EmailIngestService,
        org_id: UUID,
        connection_id: UUID,
        run_id: UUID,
        batch: FetchBatch,
        trackers: dict[str, _FolderTracker],
        counts: _Counts,
    ) -> bool:
        """Ingest a batch (savepoint/email), advance + UPSERT the cursor, commit. False ⇒ stolen."""
        # Use the resume tracker only while the generation matches; a NEW folder or a UIDVALIDITY
        # RESET starts a fresh tracker at floor 0 (the fetcher re-scans from UID 1, so the old
        # high-water floor + the old generation's failed_uids are meaningless and MUST be dropped —
        # keeping the old floor would advance the cursor past mail that was never fetched).
        tracker = trackers.get(batch.folder)
        if tracker is None or tracker.uidvalidity != batch.uidvalidity:
            tracker = _FolderTracker(uidvalidity=batch.uidvalidity, floor=0)
            trackers[batch.folder] = tracker
        # The REQUESTED uids are the advance baseline; any not accounted below (because the server
        # didn't return them) becomes a gap that stops the cursor and is retried next run.
        tracker.note_requested(batch.requested_uids)
        for message in batch.messages:
            counts.seen += 1
            outcome = await self._ingest_one(session, ingest, batch.folder, message)
            if outcome == IngestOutcome.STORED:
                counts.stored += 1
                tracker.account(message.uid)
            elif outcome == IngestOutcome.SKIPPED:
                counts.skipped += 1
                tracker.account(message.uid)
            else:  # "failed" — NOT accounted, so it stays a gap and is re-fetched next run
                counts.failed += 1
                tracker.fail(message.uid)

        advance = highest_contiguous_uid(tracker.candidates, tracker.accounted, tracker.floor)
        # Persist the failed-UID operator signal, capped to bound the cursor row. Keep the LOWEST
        # UIDs: the lowest failure is the one stalling the cursor (the most useful diagnostic). Safe
        # to trim either way — the advance (above) is independent of this list, so eviction never
        # affects correctness.
        failed_signal = sorted(tracker.failed)[:MAX_FAILED_UIDS]
        await cursors.upsert(
            org_id, connection_id, batch.folder, batch.uidvalidity, advance, failed_signal
        )
        if not await connections.set_synced_count(org_id, connection_id, run_id, counts.seen):
            return False  # claim stolen — abort (the caller rolls back this in-flight batch)
        await session.commit()
        tracker.floor = advance
        tracker.candidates = [uid for uid in tracker.candidates if uid > advance]  # bound memory
        return True

    async def _ingest_one(
        self,
        session: AsyncSession,
        ingest: EmailIngestService,
        folder: str,
        message: FetchedMessage,
    ) -> IngestOutcome | str:
        """Ingest one email in a SAVEPOINT; returns the IngestOutcome, or 'failed' if poison."""
        try:
            async with session.begin_nested():
                return await ingest.ingest_email(message.raw_bytes, message.internal_date)
        except IntegrityError as exc:
            if _is_dedup_collision(exc):
                return IngestOutcome.SKIPPED  # the row already exists (a concurrent-writer race)
            # Any OTHER constraint violation → 'failed': the runner retries it next run (a transient
            # blip recovers) and dead-letters it only after a second failure (never a silent drop).
            logger.warning(
                "Sync: email uid %s in folder %s hit a constraint violation — will retry",
                message.uid,
                folder,
            )
            return "failed"
        except Exception:
            logger.warning(
                "Sync: email uid %s in folder %s failed to ingest — will retry",
                message.uid,
                folder,
            )
            return "failed"

    async def _heartbeat_loop(self, org_id: UUID, connection_id: UUID, run_id: UUID) -> None:
        """Refresh the fenced heartbeat on its OWN session until cancelled or the claim is lost."""
        try:
            async with scoped_session(org_id) as session:
                connections = ConnectorConnectionRepository(session)
                while True:
                    await asyncio.sleep(self._heartbeat_seconds)
                    alive = await connections.heartbeat(org_id, connection_id, run_id)
                    await session.commit()
                    if not alive:
                        return  # the claim was stolen — stop beating
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Sync heartbeat for run %s failed", run_id)

    @staticmethod
    async def _stop_ticker(ticker: asyncio.Task[None]) -> None:
        """Cancel + await the heartbeat ticker, swallowing its cancellation/teardown errors."""
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await ticker

    async def _finalize(
        self,
        session: AsyncSession,
        connections: ConnectorConnectionRepository,
        runs: ConnectorSyncRunRepository,
        org_id: UUID,
        connection_id: UUID,
        run_id: UUID,
        *,
        ok: bool,
        error: str | None,
        counts: _Counts,
        started_monotonic: float,
    ) -> None:
        """Fenced finalize: connection → idle/error, ledger → done/failed, audited. Rollback if
        stolen.

        A WON finalize also records the `sync.finished` audit row on this same session, so it
        commits atomically with the outcome (status + counts + duration; content-blind). A lost
        fence records nothing — the rightful owner finalizes (and audits) the run itself.
        """
        status = "idle" if ok else "error"
        won = await connections.finalize_sync(
            org_id, connection_id, run_id, status=status, error=error
        )
        if not won:
            await session.rollback()  # claim stolen at the very end — the new run owns the row
            return
        run_status = "succeeded" if ok else "failed"
        await runs.finalize(
            org_id,
            run_id,
            status=run_status,
            seen=counts.seen,
            stored=counts.stored,
            skipped=counts.skipped,
            failed=counts.failed,
            error=error,
        )
        await AuditService(AuditRepository(session)).record(
            AuditEvent(
                action=AuditAction.SYNC_FINISHED,
                actor_type=AuditActorType.system,  # background task — no human actor to attribute
                org_id=org_id,
                entity_type=_ENTITY_CONNECTION,
                entity_id=connection_id,
                details={
                    "run_id": str(run_id),
                    "status": run_status,
                    "messages_seen": counts.seen,
                    "messages_stored": counts.stored,
                    "messages_skipped": counts.skipped,
                    "messages_failed": counts.failed,
                    "duration_seconds": round(time.monotonic() - started_monotonic, 3),
                },
            )
        )
        await session.commit()

    async def _finalize_failure(
        self, org_id: UUID, connection_id: UUID, run_id: UUID, started_monotonic: float
    ) -> None:
        """Best-effort error finalize on a FRESH session (the run's session may be unusable)."""
        try:
            async with scoped_session(org_id) as session:
                connections = ConnectorConnectionRepository(session)
                runs = ConnectorSyncRunRepository(session)
                await self._finalize(
                    session,
                    connections,
                    runs,
                    org_id,
                    connection_id,
                    run_id,
                    ok=False,
                    error="Sync failed unexpectedly.",
                    counts=_Counts(),
                    started_monotonic=started_monotonic,
                )
        except Exception:
            logger.exception("Could not finalize the failed sync run %s", run_id)

    def _build_connector(self, connection: ConnectorConnection) -> object:
        """Decrypt the credential and build the connector from the registry (mirrors service)."""
        secret = self._cipher.decrypt(connection.secret_ciphertext)
        config = {**(connection.config or {}), "username": connection.username}
        return self._registry.create(ConnectorType(connection.connector_type), config, secret)
