"""SUITE B — Sync lifecycle, claim & resumability (runner / service mode).

Runs TC-IM-B02..B10 as individual PASS/FAIL checks against the LIVE migrated DB inside the
backend container. Each case measures DB state (email counts, cursor values, error type,
sync_status / sync_run_id, ledger status) and lets the measurement decide the verdict — NO
pre-judged asserts. The ConnectorSyncRunner self-opens its OWN tenant (RLS-enforced) session;
we seed + assert + clean up on the BYPASSRLS global engine (GlobalSessionLocal), exactly like
the runner conftest's db_session.

Run (testing/ is NOT mounted; pipe over stdin into the backend container):
    docker compose exec -T backend python - < testing/10_imap-connector/harness/sync_lifecycle_suite.py

Safety: every org is a RUN-STAMPED throwaway uuid4 (collected in _STAMPED_ORGS). Cleanup in the
finally block deletes ONLY those orgs' connector_connection rows (CASCADE clears cursor / run /
email rows) + any person/company rows for those orgs (the entity graph does NOT cascade from the
connection). Creates / truncates NO shared schema. Never touches the demo orgs or real data.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base.incremental_fetch import FetchBatch, FetchedMessage, FolderCursor
from app.connectors.base.registry import ConnectorRegistry
from app.connectors.enums import ConnectorType
from app.connectors.imap.models.email import EmailMessage
from app.connectors.imap.services.email_ingest_service import EmailIngestService, IngestOutcome
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
from app.connectors.sync import connector_sync_runner as runner_module
from app.connectors.sync.connector_sync_runner import ConnectorSyncRunner
from app.core.database import GlobalSessionLocal
from app.entities.models.company import Company
from app.entities.models.person import Person

# Quiet the runner's expected warning/exception logs (we assert on DB state, not log noise).
logging.disable(logging.CRITICAL)

_STAMPED_ORGS: list[UUID] = []
_CIPHER = CredentialCipher("suite-b-runner-key-not-secure-but-long-enough", require_secure=False)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} :: {detail}")


# ── Inlined fakes (the conftest imports pytest fixtures; keep this script self-contained) ──


def eml(message_id: str, *, frm: str = "boyan@globex.com", to: str = "owner@acme.com") -> bytes:
    raw = f"From: {frm}\r\nTo: {to}\r\nSubject: S\r\nMessage-ID: <{message_id}>\r\n\r\nHello."
    return raw.encode("utf-8")


def message(uid: int, message_id: str) -> FetchedMessage:
    return FetchedMessage(uid=uid, raw_bytes=eml(message_id), internal_date=None)


def folder_batch(
    folder: str,
    uidvalidity: int,
    *messages: FetchedMessage,
    requested_uids: list[int] | None = None,
) -> FetchBatch:
    returned = list(messages)
    return FetchBatch(
        folder=folder,
        uidvalidity=uidvalidity,
        messages=returned,
        requested_uids=requested_uids if requested_uids is not None else [m.uid for m in returned],
    )


class FakeIncrementalConnector:
    """A SupportsIncrementalFetch stand-in: streams canned batches, records the cursors it saw."""

    def __init__(self, batches: list[FetchBatch], *, pending: int | None = None) -> None:
        self._batches = batches
        self._pending = pending
        self.count_cursors: list[dict[str, FolderCursor]] = []
        self.fetch_cursors: list[dict[str, FolderCursor]] = []

    async def count_pending(self, cursors: Mapping[str, FolderCursor]) -> int:
        self.count_cursors.append(dict(cursors))
        if self._pending is not None:
            return self._pending
        return sum(len(batch.messages) for batch in self._batches)

    async def fetch_incremental(
        self, cursors: Mapping[str, FolderCursor]
    ) -> AsyncIterator[FetchBatch]:
        self.fetch_cursors.append(dict(cursors))
        for batch in self._batches:
            yield batch


class GatedConnector(FakeIncrementalConnector):
    """Like FakeIncrementalConnector but blocks on an asyncio.Event between batches.

    Lets the test interleave an out-of-band mutation (disable / delete / corrupt run_id) AFTER the
    first batch has committed but BEFORE the runner streams the next one — modelling a concurrent
    admin action mid-run on a single event loop (no real threads).
    """

    def __init__(self, batches: list[FetchBatch], gate: asyncio.Event, resume: asyncio.Event):
        super().__init__(batches)
        self._gate = gate  # set by the runner after it yields the first batch
        self._resume = resume  # awaited by the runner before yielding the rest

    async def fetch_incremental(
        self, cursors: Mapping[str, FolderCursor]
    ) -> AsyncIterator[FetchBatch]:
        self.fetch_cursors.append(dict(cursors))
        first = True
        for batch in self._batches:
            yield batch
            if first:
                first = False
                self._gate.set()  # tell the test "batch 1 is committed"
                await self._resume.wait()  # hold until the test has mutated state


def make_registry(connector: FakeIncrementalConnector) -> ConnectorRegistry:
    registry = ConnectorRegistry()
    registry.register(ConnectorType.imap, lambda config, secret: connector)
    return registry


def make_runner(connector: FakeIncrementalConnector) -> ConnectorSyncRunner:
    # heartbeat_seconds huge so the ticker never fires; staleness is driven via heartbeat aging.
    return ConnectorSyncRunner(_CIPHER, make_registry(connector), heartbeat_seconds=3600)


async def seed_connection(
    session: AsyncSession, org_id: UUID, *, disabled: bool = False
) -> ConnectorConnection:
    """Insert a connector_connection with a REAL encrypted credential (runner can decrypt)."""
    connection = ConnectorConnection(
        org_id=org_id,
        connector_type="imap",
        display_name="Mailbox",
        auth_method="app_password",
        username="owner@acme.com",
        config={"host": "mail.example.com", "port": 993, "use_ssl": True},
        secret_ciphertext=_CIPHER.encrypt("app-password"),
        secret_key_version=_CIPHER.key_version,
        status="configured",
        disabled_at=datetime.now(UTC) if disabled else None,
    )
    session.add(connection)
    await session.flush()
    return connection


def new_org() -> UUID:
    org = uuid4()
    _STAMPED_ORGS.append(org)
    return org


async def claim_and_open_ledger(
    session: AsyncSession, org_id: UUID, connection_id: UUID, run_id: UUID
) -> bool:
    """Claim the slot + open the ledger row, exactly as SyncService does before spawning."""
    claimed = await ConnectorConnectionRepository(session).claim_for_sync(
        org_id, connection_id, run_id, stale_seconds=300
    )
    await ConnectorSyncRunRepository(session).start_run(org_id, connection_id, run_id)
    return claimed


async def col(session: AsyncSession, connection_id: UUID, column: object) -> object:
    return (
        await session.execute(select(column).where(ConnectorConnection.id == connection_id))
    ).scalar_one()


async def email_count(session: AsyncSession, org_id: UUID) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(EmailMessage).where(EmailMessage.org_id == org_id)
        )
    ).scalar_one()


async def age_heartbeat(session: AsyncSession, connection_id: UUID, minutes: int) -> None:
    await session.execute(
        update(ConnectorConnection)
        .where(ConnectorConnection.id == connection_id)
        .values(sync_heartbeat_at=datetime.now(UTC) - timedelta(minutes=minutes))
    )


# ────────────────────────────── TC-IM-B02 ──────────────────────────────
async def tc_b02_disable_midrun_does_not_stop_inflight(session: AsyncSession) -> None:
    """Disable AFTER claim does NOT stop an in-flight run: it still ingests + advances cursor."""
    org = new_org()
    connection = await seed_connection(session, org)
    run_id = uuid4()
    await claim_and_open_ledger(session, org, connection.id, run_id)
    await session.commit()

    # Admin disables the connection AFTER the claim is committed (the real sequence).
    await session.execute(
        update(ConnectorConnection)
        .where(ConnectorConnection.id == connection.id)
        .values(disabled_at=datetime.now(UTC))
    )
    await session.commit()

    connector = FakeIncrementalConnector([folder_batch("INBOX", 100, message(1, "b02@x"))])
    await make_runner(connector).run(org, connection.id, run_id)

    stored = await email_count(session, org)
    status = await col(session, connection.id, ConnectorConnection.sync_status)
    last_synced = await col(session, connection.id, ConnectorConnection.last_synced_at)
    disabled = await col(session, connection.id, ConnectorConnection.disabled_at)
    ingested_into_disabled = stored == 1 and status == "idle" and last_synced is not None
    check(
        "B02_disable_midrun_does_not_stop_inflight",
        ingested_into_disabled and disabled is not None,
        f"stored={stored} sync_status={status} last_synced_set={last_synced is not None} "
        f"disabled_at_set={disabled is not None}",
    )


# ────────────────────────────── TC-IM-B03 ──────────────────────────────
class _DisableInWindowRepo(ConnectorConnectionRepository):
    """Sets disabled_at IN THE WINDOW between the service's disabled-check and the claim UPDATE."""

    async def claim_for_sync(
        self, org_id: UUID, connection_id: UUID, run_id: UUID, stale_seconds: int
    ) -> bool:
        # The service already passed its `disabled_at is None` guard (get_in_org saw it active).
        # Now the admin disables the connection — same uncommitted txn, visible to the next stmt.
        await self._session.execute(
            update(ConnectorConnection)
            .where(ConnectorConnection.id == connection_id)
            .values(disabled_at=datetime.now(UTC))
        )
        return await super().claim_for_sync(org_id, connection_id, run_id, stale_seconds)


async def tc_b03_toctou_disable_returns_wrong_error(session: AsyncSession) -> None:
    """TOCTOU: disable lands AFTER the service's disabled-check but BEFORE the claim → the claim's
    disabled-IS-NULL predicate fails → SyncAlreadyRunningError (wrong) not ConnectorDisabledError."""
    from app.connectors.exceptions import ConnectorDisabledError, SyncAlreadyRunningError
    from app.connectors.sync.sync_service import SyncService

    org = new_org()
    connection = await seed_connection(session, org)  # NOT disabled — passes the service guard
    await session.commit()

    service = SyncService(
        session=session,
        connections=_DisableInWindowRepo(session),
        runs=ConnectorSyncRunRepository(session),
        runner=make_runner(FakeIncrementalConnector([])),
        spawn=lambda coro, *, label: _drop_coro(coro),
    )
    raised: str = "none"
    try:
        await service.start_sync(org, connection.id)
    except SyncAlreadyRunningError:
        raised = "SyncAlreadyRunningError"
    except ConnectorDisabledError:
        raised = "ConnectorDisabledError"
    except Exception as exc:  # noqa: BLE001 — record any other type honestly
        raised = type(exc).__name__
    await session.rollback()

    # The DEFECT is the misleading 409: "already running" when the truth is "disabled".
    check(
        "B03_toctou_disable_returns_misleading_already_running",
        raised == "SyncAlreadyRunningError",
        f"raised={raised} (expected ConnectorDisabledError; SyncAlreadyRunningError = the defect)",
    )


def _drop_coro(coro: object) -> None:
    """Close an un-awaited coroutine so it never schedules (B03's spawn is a no-op)."""
    with contextlib.suppress(Exception):
        coro.close()  # type: ignore[attr-defined]


# ────────────────────────────── TC-IM-B04 ──────────────────────────────
async def tc_b04_stale_window_reclaim_double_count(session: AsyncSession) -> None:
    """Stale-claim window: age a LIVE claim's heartbeat >5min, a second claim succeeds. Does the
    second runner DOUBLE-COUNT the same mail? Measure the email rows — the fence + dedup should hold."""
    org = new_org()
    connection = await seed_connection(session, org)
    run_a = uuid4()
    await claim_and_open_ledger(session, org, connection.id, run_a)
    await session.commit()

    # Run A ingests its batch (cursor + emails committed).
    connector_a = FakeIncrementalConnector(
        [folder_batch("INBOX", 100, message(1, "b04a@x"), message(2, "b04b@x"))]
    )
    await make_runner(connector_a).run(org, connection.id, run_a)
    after_a = await email_count(session, org)

    # Age the (now finalized) heartbeat past the stale window and reclaim with a SECOND run that
    # re-streams the SAME UIDs (simulating two runners racing the same mail).
    await age_heartbeat(session, connection.id, minutes=10)
    run_b = uuid4()
    reclaimed = await claim_and_open_ledger(session, org, connection.id, run_b)
    await session.commit()
    connector_b = FakeIncrementalConnector(
        [folder_batch("INBOX", 100, message(1, "b04a@x"), message(2, "b04b@x"))]
    )
    await make_runner(connector_b).run(org, connection.id, run_b)
    after_b = await email_count(session, org)

    # Honest measure: re-ingest must be idempotent (dedup) → no double count even after reclaim.
    no_double_count = after_a == 2 and after_b == 2
    check(
        "B04_stale_reclaim_no_double_count",
        reclaimed and no_double_count,
        f"reclaimed={reclaimed} emails_after_runA={after_a} emails_after_runB={after_b} "
        f"(double-count would be 4)",
    )


# ────────────────────────────── TC-IM-B05 ──────────────────────────────
async def tc_b05_delete_during_run_aborts_clean(session: AsyncSession) -> None:
    """DELETE the connection mid-run → CASCADE purges email/cursor/run; the runner's next fenced
    write hits 0 rows / get_in_org None → aborts cleanly (no orphans, no unhandled crash)."""
    org = new_org()
    connection = await seed_connection(session, org)
    run_id = uuid4()
    await claim_and_open_ledger(session, org, connection.id, run_id)
    await session.commit()

    gate, resume = asyncio.Event(), asyncio.Event()
    connector = GatedConnector(
        [
            folder_batch("INBOX", 100, message(1, "b05a@x")),
            folder_batch("INBOX", 100, message(2, "b05b@x")),
        ],
        gate,
        resume,
    )
    run_task = asyncio.create_task(make_runner(connector).run(org, connection.id, run_id))
    await gate.wait()  # batch 1 committed; runner paused before batch 2

    # Admin deletes the connection now — CASCADE removes cursor/run/email rows.
    await session.execute(
        delete(ConnectorConnection).where(ConnectorConnection.id == connection.id)
    )
    await session.commit()
    resume.set()
    await run_task  # run() never raises; must return cleanly

    # No orphans: the email row from batch 1 was cascade-deleted with the connection.
    orphan_emails = await email_count(session, org)
    orphan_cursors = (
        await session.execute(
            select(func.count())
            .select_from(text("connector_sync_cursor"))
            .where(text("connection_id = :cid")),
            {"cid": connection.id},
        )
    ).scalar_one()
    gone = (
        await session.execute(
            select(func.count())
            .select_from(ConnectorConnection)
            .where(ConnectorConnection.id == connection.id)
        )
    ).scalar_one()
    check(
        "B05_delete_during_run_aborts_clean",
        run_task.done()
        and not run_task.cancelled()
        and run_task.exception() is None
        and orphan_emails == 0
        and orphan_cursors == 0
        and gone == 0,
        f"run_clean={run_task.exception() is None} orphan_emails={orphan_emails} "
        f"orphan_cursors={orphan_cursors} connection_rows={gone}",
    )


# ────────────────────────────── TC-IM-B06 ──────────────────────────────
async def tc_b06_uidvalidity_reset_resets_floor(session: AsyncSession) -> None:
    """Cursor resume + UIDVALIDITY change → re-scan from UID 1, cursor lands on the NEW high-water
    (3), never advancing past the stale pre-reset floor (1000). Reconfirm of an existing test."""
    org = new_org()
    connection = await seed_connection(session, org)
    await ConnectorSyncCursorRepository(session).upsert(
        org, connection.id, "INBOX", uidvalidity=111, last_seen_uid=1000, failed_uids=[]
    )
    run_id = uuid4()
    await claim_and_open_ledger(session, org, connection.id, run_id)
    await session.commit()

    connector = FakeIncrementalConnector(  # NEW uidvalidity 222 → re-scan from UID 1
        [folder_batch("INBOX", 222, message(1, "b06a@x"), message(2, "b06b@x"), message(3, "b06c@x"))]
    )
    await make_runner(connector).run(org, connection.id, run_id)

    cursor = (await ConnectorSyncCursorRepository(session).list_for_connection(org, connection.id))[
        "INBOX"
    ]
    check(
        "B06_uidvalidity_reset_resets_floor",
        cursor.uidvalidity == 222 and cursor.last_seen_uid == 3,
        f"uidvalidity={cursor.uidvalidity} last_seen_uid={cursor.last_seen_uid} "
        f"(bug would leave last_seen_uid=1000)",
    )


# ────────────────────────────── TC-IM-B07 ──────────────────────────────
async def tc_b07_gap_stops_cursor(session: AsyncSession) -> None:
    """A requested-but-unreturned UID (dropped FETCH) → highest_contiguous_uid stops at the gap;
    cursor does not skip past it. Reconfirm of an existing test."""
    org = new_org()
    connection = await seed_connection(session, org)
    run_id = uuid4()
    await claim_and_open_ledger(session, org, connection.id, run_id)
    await session.commit()

    connector = FakeIncrementalConnector(
        [folder_batch("INBOX", 100, message(1, "b07a@x"), message(3, "b07c@x"), requested_uids=[1, 2, 3])]
    )
    await make_runner(connector).run(org, connection.id, run_id)

    cursor = (await ConnectorSyncCursorRepository(session).list_for_connection(org, connection.id))[
        "INBOX"
    ]
    stored = await email_count(session, org)
    check(
        "B07_gap_stops_cursor_no_skip",
        cursor.last_seen_uid == 1 and stored == 2,
        f"last_seen_uid={cursor.last_seen_uid} (must be 1, not 3) stored={stored} "
        f"(UID 2 retried next run)",
    )


# ────────────────────────────── TC-IM-B08 ──────────────────────────────
class _FailOneUidIngest:
    """An EmailIngestService stand-in that raises a NON-dedup IntegrityError for one Message-ID.

    Stands in for a TRANSIENT fault (deadlock / connection blip) on a single email. The runner does
    NOT distinguish transient from permanent, so it routes the error to tracker.fail → the UID is
    persisted to failed_uids and STEPPED OVER FOREVER = silent permanent mail loss.
    """

    _poison_message_id = "b08poison@x"

    def __init__(self, session: AsyncSession, connection: ConnectorConnection) -> None:
        self._real = EmailIngestService(session, connection)

    async def ingest_email(self, raw_bytes: bytes, internal_date: object = None) -> IngestOutcome:
        if self._poison_message_id.encode() in raw_bytes:
            # A non-dedup constraint name → the runner's _is_dedup_collision returns False → 'failed'.
            raise IntegrityError(
                "stmt", {}, Exception('violates check constraint "ck_some_transient_thing"')
            )
        return await self._real.ingest_email(raw_bytes, internal_date)


async def tc_b08_transient_error_steps_over_forever(session: AsyncSession) -> None:
    """A misclassified transient (non-dedup) error on one UID → failed_uids → skipped every run."""
    org = new_org()
    connection = await seed_connection(session, org)
    run_id = uuid4()
    await claim_and_open_ledger(session, org, connection.id, run_id)
    await session.commit()

    original = runner_module.EmailIngestService
    runner_module.EmailIngestService = _FailOneUidIngest  # type: ignore[assignment,misc]
    try:
        # UID 2 is the poison; 1 and 3 store fine. Gap-stop would normally hold the cursor at 1,
        # but because UID 2 is routed to FAILED (not a gap) the cursor steps over it to 3.
        connector = FakeIncrementalConnector(
            [
                folder_batch(
                    "INBOX",
                    100,
                    message(1, "b08a@x"),
                    message(2, "b08poison@x"),
                    message(3, "b08c@x"),
                )
            ]
        )
        await make_runner(connector).run(org, connection.id, run_id)
    finally:
        runner_module.EmailIngestService = original  # type: ignore[misc]

    cursor = (await ConnectorSyncCursorRepository(session).list_for_connection(org, connection.id))[
        "INBOX"
    ]
    stored = await email_count(session, org)
    # The defect: UID 2 is in failed_uids AND the cursor advanced PAST it (to 3) → never re-fetched.
    permanently_lost = (
        2 in cursor.failed_uids and cursor.last_seen_uid == 3 and stored == 2
    )
    check(
        "B08_transient_error_steps_over_uid_forever",
        permanently_lost,
        f"failed_uids={cursor.failed_uids} last_seen_uid={cursor.last_seen_uid} stored={stored} "
        f"(UID 2 lost: cursor stepped to 3, 2 recorded failed → never retried)",
    )


# ────────────────────────────── TC-IM-B09 ──────────────────────────────
async def tc_b09_failed_uids_grows_unbounded(session: AsyncSession) -> None:
    """failed_uids ARRAY grows across runs with no cap (cleared only on a UIDVALIDITY reset)."""
    org = new_org()
    connection = await seed_connection(session, org)
    cursors = ConnectorSyncCursorRepository(session)

    # Simulate many runs each persisting a fresh permanently-failed UID under the SAME generation.
    accumulated: list[int] = []
    for uid in range(1, 51):  # 50 runs, each adds one poison UID
        accumulated.append(uid)
        await cursors.upsert(org, connection.id, "INBOX", uidvalidity=100, last_seen_uid=uid, failed_uids=list(accumulated))
    await session.commit()
    grown = (await cursors.list_for_connection(org, connection.id))["INBOX"]

    # A UIDVALIDITY reset is the ONLY thing that drops the array (fresh tracker at floor 0).
    await cursors.upsert(org, connection.id, "INBOX", uidvalidity=200, last_seen_uid=0, failed_uids=[])
    await session.commit()
    after_reset = (await cursors.list_for_connection(org, connection.id))["INBOX"]

    check(
        "B09_failed_uids_unbounded_until_uidvalidity_reset",
        len(grown.failed_uids) == 50 and len(after_reset.failed_uids) == 0,
        f"failed_uids_len_after_50_runs={len(grown.failed_uids)} (no cap) "
        f"len_after_uidvalidity_reset={len(after_reset.failed_uids)}",
    )


# ────────────────────────────── TC-IM-B10 ──────────────────────────────
async def tc_b10_corrupt_run_id_wedges_then_self_heals(session: AsyncSession) -> None:
    """Corrupt sync_run_id to NULL on a live 'running' claim: every fenced write now fails (NULL !=
    run_id) AND a fresh claim is blocked (status still 'running', heartbeat fresh) → wedged. Aging
    the heartbeat past STALE_SECONDS lets a reclaim through (self-heals). Bounded by the window."""
    org = new_org()
    connection = await seed_connection(session, org)
    run_a = uuid4()
    await claim_and_open_ledger(session, org, connection.id, run_a)
    await session.commit()

    repo = ConnectorConnectionRepository(session)
    # Heartbeat still owned by run_a before corruption.
    owned_before = await repo.heartbeat(org, connection.id, run_a)
    await session.commit()

    # Corrupt the fencing token to NULL while leaving sync_status='running'.
    await session.execute(
        update(ConnectorConnection)
        .where(ConnectorConnection.id == connection.id)
        .values(sync_run_id=None)
    )
    await session.commit()

    # Fenced writes now miss (sync_run_id == run_a no longer matches NULL).
    fenced_after = await repo.heartbeat(org, connection.id, run_a)
    await session.commit()
    # A fresh claimant is blocked: status='running' AND heartbeat is fresh. A failed claim
    # (rowcount 0) mutates nothing, so committing the no-op is safe (no rollback needed).
    blocked_claim = await repo.claim_for_sync(org, connection.id, uuid4(), stale_seconds=300)
    await session.commit()

    # Self-heal: age the heartbeat past the stale window → a reclaim is now possible.
    await age_heartbeat(session, connection.id, minutes=10)
    await session.commit()
    run_c = uuid4()
    healed_claim = await repo.claim_for_sync(org, connection.id, run_c, stale_seconds=300)
    await session.commit()

    wedged_then_healed = (
        owned_before and not fenced_after and not blocked_claim and healed_claim
    )
    check(
        "B10_corrupt_run_id_wedges_until_stale_window",
        wedged_then_healed,
        f"owned_before={owned_before} fenced_write_after_corrupt={fenced_after} "
        f"fresh_claim_blocked_while_fresh={not blocked_claim} reclaim_after_stale={healed_claim}",
    )


async def cleanup() -> None:
    """Delete ONLY this run's rows: connector_connection (CASCADE → cursor/run/email) + person/company."""
    async with GlobalSessionLocal() as session:
        for org in _STAMPED_ORGS:
            await session.execute(delete(ConnectorConnection).where(ConnectorConnection.org_id == org))
            await session.execute(delete(Person).where(Person.org_id == org))
            await session.execute(delete(Company).where(Company.org_id == org))
        await session.commit()
    print(f"cleanup: deleted rows for {len(_STAMPED_ORGS)} run-stamped orgs (CASCADE)")


async def main() -> None:
    cases = [
        tc_b02_disable_midrun_does_not_stop_inflight,
        tc_b03_toctou_disable_returns_wrong_error,
        tc_b04_stale_window_reclaim_double_count,
        tc_b05_delete_during_run_aborts_clean,
        tc_b06_uidvalidity_reset_resets_floor,
        tc_b07_gap_stops_cursor,
        tc_b08_transient_error_steps_over_forever,
        tc_b09_failed_uids_grows_unbounded,
        tc_b10_corrupt_run_id_wedges_then_self_heals,
    ]
    try:
        for case in cases:
            # Each case gets its OWN global session (seed/assert/cleanup), like the conftest db_session.
            async with GlobalSessionLocal() as session:
                try:
                    await case(session)
                except Exception as exc:  # noqa: BLE001 — record a crashed case honestly, don't abort
                    await session.rollback()
                    check(case.__name__, False, f"CASE CRASHED: {type(exc).__name__}: {exc}")
    finally:
        await cleanup()

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\nRESULT: {passed}/{len(results)} checks passed")


asyncio.run(main())
