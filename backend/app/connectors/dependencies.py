"""
Role: FastAPI dependency wiring for the connectors module — the shared connector registry, the
      credential cipher, and the ConnectorService provider (on the caller's tenant session).
Used by: routes.connector_routes.
Depends on: app.core.config (settings/key), app.identity.dependencies (auth seam:
            get_tenant_session derives org_id from the verified JWT), the connector
            repository / service / security / registry.
Key invariants:
  - The registry is built ONCE at import (build_default_registry never raises) and reused — a
    process-wide, read-mostly table of connector factories.
  - The cipher is constructed per request from settings.connector_secret_key with
    require_secure=settings.requires_secure_secrets, so a misconfigured non-dev env fails closed
    (503) the moment a connector endpoint is hit — without blocking boot for connector-less
    deployments.
  - The service runs on the TENANT-scoped session (get_tenant_session): org_id comes only from
    the verified JWT, never a header/body. get_connector_registry is a separate provider so tests
    can override it with a fake-client registry (the vendor mock boundary).
"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base.registry import ConnectorRegistry, build_default_registry
from app.connectors.repositories.connector_connection_repository import (
    ConnectorConnectionRepository,
)
from app.connectors.repositories.connector_sync_run_repository import ConnectorSyncRunRepository
from app.connectors.security.credential_cipher import CredentialCipher
from app.connectors.services.connector_service import ConnectorService
from app.connectors.sync.connector_sync_runner import ConnectorSyncRunner
from app.connectors.sync.sync_service import SyncService
from app.connectors.sync.sync_task_registry import SyncTaskSpawner, spawn_sync_task
from app.core.config import get_settings
from app.identity.dependencies import get_tenant_session

# Built once per process; defensive discovery means this never raises (see build_default_registry).
_REGISTRY = build_default_registry()


def get_connector_registry() -> ConnectorRegistry:
    """Provide the process-wide connector registry (overridable in tests for a fake client)."""
    return _REGISTRY


def get_connector_service(
    session: AsyncSession = Depends(get_tenant_session),
    registry: ConnectorRegistry = Depends(get_connector_registry),
) -> ConnectorService:
    """Provide ConnectorService on the caller's TENANT-scoped session.

    The credential cipher is built from settings and fails closed (503) in any non-dev env that
    still uses the dev/weak key — but only here, on actual connector use, never at boot.
    """
    settings = get_settings()
    cipher = CredentialCipher(
        settings.connector_secret_key, require_secure=settings.requires_secure_secrets
    )
    return ConnectorService(
        connections=ConnectorConnectionRepository(session),
        cipher=cipher,
        registry=registry,
    )


def get_sync_task_spawner() -> SyncTaskSpawner:
    """Provide the background-task spawner (a separate provider so tests override it to capture)."""
    return spawn_sync_task


def get_sync_service(
    session: AsyncSession = Depends(get_tenant_session),
    registry: ConnectorRegistry = Depends(get_connector_registry),
    spawn: SyncTaskSpawner = Depends(get_sync_task_spawner),
) -> SyncService:
    """Provide SyncService on the caller's TENANT-scoped session.

    The runner it spawns opens its OWN tenant session (it outlives this request), so it carries
    only the process-wide registry + the (stateless) cipher — never this request's session. The
    cipher is built here and fails closed (503) on a weak key in any non-dev env, like the
    connector service. get_sync_task_spawner is a separate provider so a test can swap the spawn
    for a capture (no real background task) the same way it swaps the registry.
    """
    settings = get_settings()
    cipher = CredentialCipher(
        settings.connector_secret_key, require_secure=settings.requires_secure_secrets
    )
    runner = ConnectorSyncRunner(cipher, registry)
    return SyncService(
        session=session,
        connections=ConnectorConnectionRepository(session),
        runs=ConnectorSyncRunRepository(session),
        runner=runner,
        spawn=spawn,
    )
