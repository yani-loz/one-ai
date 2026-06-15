"""
Role: Tier-3 self-connect endpoints (CO-01 /me/connectors) — any authenticated user connects,
      lists, tests, syncs, and disconnects/erases THEIR OWN connections. Routes parse + delegate +
      return (rule A5); all logic is in MeConnectorService.
Used by: app.connectors.router (aggregated into connectors_router).
Depends on: connectors.dependencies (get_me_connector_service), me_connector_schemas, connector_
            schemas (ConnectionResponse / SyncStatusResponse), identity.dependencies
            (get_current_principal — ANY user, member or admin), identity.Principal.
Key invariants:
  - Gated by get_current_principal (member OR admin — NOT require_company_admin): every user owns
    their self-connect plane. The owner is principal.subject_id; org is principal.org_id (both from
    the verified JWT, never a header/body).
  - PER-USER ISOLATION: the service loads via get_for_owner, so another user's connection is a 404.
  - Responses are metadata (ConnectionResponse — the OWNER may see their own params); no secret.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.connectors.dependencies import get_me_connector_service
from app.connectors.schemas.connector_schemas import ConnectionResponse, SyncStatusResponse
from app.connectors.schemas.me_connector_schemas import (
    AllowedConnectorTypeResponse,
    SelfConnectRequest,
)
from app.connectors.services.me_connector_service import MeConnectorService
from app.identity.dependencies import get_current_principal
from app.identity.principal import Principal

router = APIRouter(prefix="/me/connectors", tags=["connectors-me"])


@router.get("/types", response_model=list[AllowedConnectorTypeResponse])
async def list_allowed_types(
    principal: Principal = Depends(get_current_principal),
    service: MeConnectorService = Depends(get_me_connector_service),
) -> list[AllowedConnectorTypeResponse]:
    """List which connector types the calling user may self-connect (drives the panel cards)."""
    return await service.list_allowed_types(principal.org_id, principal.subject_id)


@router.get("", response_model=list[ConnectionResponse])
async def list_my_connections(
    principal: Principal = Depends(get_current_principal),
    service: MeConnectorService = Depends(get_me_connector_service),
) -> list[ConnectionResponse]:
    """List the caller's OWN connections (newest-first, metadata only)."""
    connections = await service.list_my_connections(principal.org_id, principal.subject_id)
    return [ConnectionResponse.from_model(connection) for connection in connections]


@router.post("", response_model=ConnectionResponse, status_code=status.HTTP_201_CREATED)
async def self_connect(
    payload: SelfConnectRequest,
    principal: Principal = Depends(get_current_principal),
    service: MeConnectorService = Depends(get_me_connector_service),
) -> ConnectionResponse:
    """Connect the caller's OWN mailbox after the permission + consent gates (403/400 if denied)."""
    connection = await service.self_connect(principal.org_id, payload, actor=principal)
    return ConnectionResponse.from_model(connection)


@router.get("/{connection_id}", response_model=ConnectionResponse)
async def get_my_connection(
    connection_id: UUID,
    principal: Principal = Depends(get_current_principal),
    service: MeConnectorService = Depends(get_me_connector_service),
) -> ConnectionResponse:
    """Get one of the caller's OWN connections (404 if it isn't theirs)."""
    connection = await service.get_my_connection(
        principal.org_id, principal.subject_id, connection_id
    )
    return ConnectionResponse.from_model(connection)


@router.post("/{connection_id}/test", response_model=ConnectionResponse)
async def test_my_connection(
    connection_id: UUID,
    principal: Principal = Depends(get_current_principal),
    service: MeConnectorService = Depends(get_me_connector_service),
) -> ConnectionResponse:
    """Verify one of the caller's OWN connections; return its status (404 if not theirs)."""
    connection = await service.test_my_connection(
        principal.org_id, principal.subject_id, connection_id
    )
    return ConnectionResponse.from_model(connection)


@router.post(
    "/{connection_id}/sync",
    response_model=SyncStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def sync_my_connection(
    connection_id: UUID,
    principal: Principal = Depends(get_current_principal),
    service: MeConnectorService = Depends(get_me_connector_service),
) -> SyncStatusResponse:
    """Trigger an incremental sync of the caller's OWN connection (202; 404 if not theirs)."""
    connection = await service.sync_my_connection(
        principal.org_id, principal.subject_id, connection_id, actor=principal
    )
    return SyncStatusResponse.from_model(connection)


@router.get("/{connection_id}/sync", response_model=SyncStatusResponse)
async def get_my_sync_status(
    connection_id: UUID,
    principal: Principal = Depends(get_current_principal),
    service: MeConnectorService = Depends(get_me_connector_service),
) -> SyncStatusResponse:
    """Poll the caller's OWN connection's live sync progress (404 if not theirs)."""
    connection = await service.get_my_sync_status(
        principal.org_id, principal.subject_id, connection_id
    )
    return SyncStatusResponse.from_model(connection)


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_my_connection(
    connection_id: UUID,
    principal: Principal = Depends(get_current_principal),
    service: MeConnectorService = Depends(get_me_connector_service),
) -> None:
    """Disconnect + erase the caller's OWN connection + its ingested data (404 if not theirs)."""
    await service.disconnect_my_connection(
        principal.org_id, principal.subject_id, connection_id, actor=principal
    )
