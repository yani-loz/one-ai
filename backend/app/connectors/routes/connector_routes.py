"""
Role: Company-admin connector endpoints — configure / list / get / test / delete a connection.
      Routes parse + delegate + return (rule A5); all business logic is in ConnectorService.
Used by: app.connectors.router (aggregated into connectors_router).
Depends on: connectors.dependencies (service provider), connectors.schemas, ConnectorService,
            app.identity.dependencies.require_company_admin (auth + role gate), identity.Principal.
Key invariants:
  - Every endpoint is gated by require_company_admin (member -> 403, missing/invalid token ->
    401) and scoped to the caller's org via principal.org_id (cross-org -> 404).
  - POST /connectors/{id}/test returns 200 with the connection's verification outcome (status +
    last_error) — a failed verification is a successful query reporting a negative result.
  - Responses are metadata only (ConnectionResponse) — no secret ever leaves here.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.connectors.dependencies import get_connector_service, get_sync_service
from app.connectors.schemas.connector_schemas import (
    ConnectionResponse,
    CreateConnectionRequest,
    SyncStatusResponse,
)
from app.connectors.services.connector_service import ConnectorService
from app.connectors.sync.sync_service import SyncService
from app.identity.dependencies import require_company_admin
from app.identity.principal import Principal

router = APIRouter(prefix="/connectors", tags=["connectors"])


@router.post("", response_model=ConnectionResponse, status_code=status.HTTP_201_CREATED)
async def create_connection(
    payload: CreateConnectionRequest,
    principal: Principal = Depends(require_company_admin),
    service: ConnectorService = Depends(get_connector_service),
) -> ConnectionResponse:
    """Configure a new connector connection for the caller's org (credential encrypted)."""
    connection = await service.create_connection(principal.org_id, payload)
    return ConnectionResponse.from_model(connection)


@router.get("", response_model=list[ConnectionResponse])
async def list_connections(
    principal: Principal = Depends(require_company_admin),
    service: ConnectorService = Depends(get_connector_service),
) -> list[ConnectionResponse]:
    """List the caller's org connections (newest-first, metadata only)."""
    connections = await service.list_connections(principal.org_id)
    return [ConnectionResponse.from_model(connection) for connection in connections]


@router.get("/{connection_id}", response_model=ConnectionResponse)
async def get_connection(
    connection_id: UUID,
    principal: Principal = Depends(require_company_admin),
    service: ConnectorService = Depends(get_connector_service),
) -> ConnectionResponse:
    """Get one of the caller's connections (404 if it isn't theirs)."""
    connection = await service.get_connection(principal.org_id, connection_id)
    return ConnectionResponse.from_model(connection)


@router.post("/{connection_id}/test", response_model=ConnectionResponse)
async def test_connection(
    connection_id: UUID,
    principal: Principal = Depends(require_company_admin),
    service: ConnectorService = Depends(get_connector_service),
) -> ConnectionResponse:
    """Verify a stored connection (connect + authenticate) and return its updated status."""
    connection = await service.test_connection(principal.org_id, connection_id)
    return ConnectionResponse.from_model(connection)


@router.post("/{connection_id}/disable", response_model=ConnectionResponse)
async def disable_connection(
    connection_id: UUID,
    principal: Principal = Depends(require_company_admin),
    service: ConnectorService = Depends(get_connector_service),
) -> ConnectionResponse:
    """Disable a connection (reversible) — stops sync + revokes AI access (404 if not theirs)."""
    connection = await service.disable_connection(principal.org_id, connection_id)
    return ConnectionResponse.from_model(connection)


@router.post("/{connection_id}/enable", response_model=ConnectionResponse)
async def enable_connection(
    connection_id: UUID,
    principal: Principal = Depends(require_company_admin),
    service: ConnectorService = Depends(get_connector_service),
) -> ConnectionResponse:
    """Re-enable a disabled connection (404 if it isn't theirs)."""
    connection = await service.enable_connection(principal.org_id, connection_id)
    return ConnectionResponse.from_model(connection)


@router.post(
    "/{connection_id}/sync",
    response_model=SyncStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_sync(
    connection_id: UUID,
    principal: Principal = Depends(require_company_admin),
    service: SyncService = Depends(get_sync_service),
) -> SyncStatusResponse:
    """Trigger an incremental sync (202): claims the single-runner slot, returns the live status.

    409 if a sync is already running or the connection is disabled; 404 if it isn't the caller's.
    """
    connection = await service.start_sync(principal.org_id, connection_id)
    return SyncStatusResponse.from_model(connection)


@router.get("/{connection_id}/sync", response_model=SyncStatusResponse)
async def get_sync_status(
    connection_id: UUID,
    principal: Principal = Depends(require_company_admin),
    service: SyncService = Depends(get_sync_service),
) -> SyncStatusResponse:
    """Poll a connection's live sync progress (the N/M the UI shows); 404 if not the caller's."""
    connection = await service.get_sync_status(principal.org_id, connection_id)
    return SyncStatusResponse.from_model(connection)


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: UUID,
    principal: Principal = Depends(require_company_admin),
    service: ConnectorService = Depends(get_connector_service),
) -> None:
    """Delete one of the caller's connections (404 if it isn't theirs)."""
    await service.delete_connection(principal.org_id, connection_id)
