"""
Role: SyncService — the company-side orchestration for triggering + observing a connector sync.
      start_sync atomically CLAIMS the single-runner slot, opens the run ledger, COMMITS the claim,
      then spawns the background ConnectorSyncRunner; get_sync_status reads the live progress row.
Used by: routes.connector_routes (POST/GET /connectors/{id}/sync); built in connectors.dependencies
         on the caller's TENANT-scoped session.
Depends on: ConnectorConnectionRepository (claim + status), ConnectorSyncRunRepository (ledger),
            ConnectorSyncRunner (the background task), sync_task_registry.spawn_sync_task, the
            connector exceptions, identity.services.audit_service (AuditService — sync.started)
            + identity.Principal/AuditActorType.
Key invariants:
  - CROSS-TENANT ISOLATION: every method takes org_id (from the verified JWT) and loads via
    get_in_org(id, org_id); another org's connection resolves to ConnectionNotFoundError (-> 404),
    never a 403/200-empty leak.
  - CLAIM-BEFORE-SPAWN, COMMITTED: the claim + ledger row are COMMITTED before the runner is
    spawned. This is the one deliberate place the service owns a commit (rule A5's unit-of-work
    boundary is the request session) — the background runner uses a SEPARATE session, so it must be
    able to SEE the committed claim, else its first fenced write would hit 0 rows and abort itself.
  - SINGLE RUNNER: claim_for_sync is the atomic gate (a fresh live run -> SyncAlreadyRunningError);
    a disabled connection is rejected up front (ConnectorDisabledError) for a clear message rather
    than relying on the claim's disabled-guard returning a generic conflict.
  - AUDITED START (report H-5): a successful claim records a `sync.started` audit row on the SAME
    session, INSIDE the claim commit — the claim can never succeed unlogged. The actor is the
    triggering Principal when available (the route), else actor_type='system' (a future scheduled
    sync). Content-blind: connection/run ids only — never the credential or mail content. The
    matching `sync.finished` row is the runner's (it owns finalize).
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.exceptions import (
    ConnectionNotFoundError,
    ConnectorDisabledError,
    ConnectorNotEntitledError,
    SyncAlreadyRunningError,
)
from app.connectors.models.connector_connection import ConnectorConnection
from app.connectors.repositories.connector_connection_repository import (
    ConnectorConnectionRepository,
)
from app.connectors.repositories.connector_entitlement_repository import (
    ConnectorEntitlementRepository,
)
from app.connectors.repositories.connector_sync_run_repository import ConnectorSyncRunRepository
from app.connectors.sync.connector_sync_runner import STALE_SECONDS, ConnectorSyncRunner
from app.connectors.sync.sync_task_registry import SyncTaskSpawner, spawn_sync_task
from app.identity.enums import AuditActorType
from app.identity.principal import Principal
from app.identity.services.audit_service import AuditAction, AuditEvent, AuditService

_ENTITY_CONNECTION = "connector_connection"


class SyncService:
    """Trigger + observe a connection's incremental sync (single-runner, fenced background task)."""

    def __init__(
        self,
        session: AsyncSession,
        connections: ConnectorConnectionRepository,
        runs: ConnectorSyncRunRepository,
        runner: ConnectorSyncRunner,
        audit: AuditService,
        entitlements: ConnectorEntitlementRepository,
        *,
        spawn: SyncTaskSpawner = spawn_sync_task,
        stale_seconds: int = STALE_SECONDS,
    ) -> None:
        """Wire the session (claim commit), repos, runner, audit, entitlement reader, and spawner.

        `entitlements` (global session) is the Tier-1 ceiling: a sync won't start for a connector
        type the company isn't entitled to — so a revoked entitlement stops ingest on the admin
        plane too (the /me plane additionally re-checks per-user access in MeConnectorService).
        """
        self._session = session
        self._connections = connections
        self._runs = runs
        self._runner = runner
        self._audit = audit
        self._entitlements = entitlements
        self._spawn = spawn
        self._stale_seconds = stale_seconds

    async def start_sync(
        self,
        org_id: UUID,
        connection_id: UUID,
        actor: Principal | None = None,
        *,
        owner_user_id: UUID | None = None,
    ) -> ConnectorConnection:
        """Claim the sync slot, open the ledger, audit, commit, and spawn the runner.

        Records a `sync.started` audit row inside the claim commit (same transaction — a
        claimed run is never unlogged). `actor` is the triggering Principal where one exists
        (the route); None means a system-triggered sync (actor_type='system'). Returns the row.

        SCOPE (CO-01): owner_user_id None = SHARED-only (the admin plane — a user-owned id 404s, so
        an admin can never decrypt + ingest an employee's mailbox). A set owner_user_id scopes to
        that user's own row (the /me plane, after its ownership gate).

        Raises:
            ConnectionNotFoundError: the connection isn't in scope (-> 404).
            ConnectorDisabledError: the connection is admin-disabled (-> 409).
            SyncAlreadyRunningError: a live run already holds the claim (-> 409).
        """
        connection = await self._load_scoped(org_id, connection_id, owner_user_id)
        if connection is None:
            raise ConnectionNotFoundError("Connection not found.")
        if not await self._entitlements.is_entitled(org_id, connection.connector_type):
            raise ConnectorNotEntitledError(
                "This connector is not included in your company's plan."
            )
        if connection.disabled_at is not None:
            raise ConnectorDisabledError("This connection is disabled — enable it before syncing.")

        run_id = uuid4()
        claimed = await self._connections.claim_for_sync(
            org_id, connection_id, run_id, self._stale_seconds
        )
        if not claimed:
            raise SyncAlreadyRunningError("A sync is already running for this connection.")
        await self._runs.start_run(org_id, connection_id, run_id)
        # Stamp any prior still-'running' ledger row (a crashed run we just reclaimed) 'abandoned'.
        await self._runs.mark_stale_running_abandoned(org_id, connection_id, keep_run_id=run_id)
        # Content-blind sync.started, riding the claim commit below (never a credential/content).
        await self._audit.record(
            AuditEvent(
                action=AuditAction.SYNC_STARTED,
                actor_type=(
                    AuditActorType(actor.subject_type)
                    if actor is not None
                    else AuditActorType.system
                ),
                actor_id=actor.subject_id if actor is not None else None,
                org_id=org_id,
                entity_type=_ENTITY_CONNECTION,
                entity_id=connection_id,
                details={"run_id": str(run_id)},
            )
        )

        # Commit the claim BEFORE spawning: the runner's separate session must see it (invariant).
        await self._session.commit()
        self._spawn(self._runner.run(org_id, connection_id, run_id), label=f"sync:{connection_id}")

        await self._session.refresh(connection)  # reload the sync_* columns the claim UPDATE set
        return connection

    async def get_sync_status(
        self, org_id: UUID, connection_id: UUID, *, owner_user_id: UUID | None = None
    ) -> ConnectorConnection:
        """Return the connection row with its live sync_* progress, or 404 if not in scope.

        SCOPE (CO-01): owner_user_id None = SHARED-only (admin plane); set = that user's own row
        (the /me plane). Mirrors start_sync so an admin can't poll a user-owned connection's sync.
        """
        connection = await self._load_scoped(org_id, connection_id, owner_user_id)
        if connection is None:
            raise ConnectionNotFoundError("Connection not found.")
        return connection

    async def _load_scoped(
        self, org_id: UUID, connection_id: UUID, owner_user_id: UUID | None
    ) -> ConnectorConnection | None:
        """Load a connection by plane: NULL owner = shared (admin); set owner = that user's own."""
        if owner_user_id is None:
            return await self._connections.get_shared_in_org(connection_id, org_id)
        return await self._connections.get_for_owner(connection_id, org_id, owner_user_id)
