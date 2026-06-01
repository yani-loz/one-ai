"""
Role: Company user-management endpoints (/users/*). Routes parse + delegate + return.
Used by: app.identity.router (aggregated into identity_router).
Depends on: identity.dependencies (require_company_admin gate, UserService provider),
            identity.schemas.user_schemas, identity.services.user_service,
            identity.principal.
Key invariants:
  - EVERY endpoint is gated by require_company_admin (member -> 403) AND the
    UserService runs on a TENANT-scoped session whose org_id is the caller's verified
    org. org_id passed to the service is principal.org_id — never from path/body.
  - A company_admin targeting another org's user resolves to 404 (UserService), never
    a 200-empty / existence leak.
  - No business logic here.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.identity.dependencies import get_user_service, require_company_admin
from app.identity.principal import Principal
from app.identity.schemas.user_schemas import (
    UserCreateRequest,
    UserResponse,
    UserUpdateRequest,
)
from app.identity.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserResponse])
async def list_users(
    principal: Principal = Depends(require_company_admin),
    service: UserService = Depends(get_user_service),
) -> list[UserResponse]:
    """List all users in the caller's organization."""
    return await service.list_users(principal.org_id)


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreateRequest,
    principal: Principal = Depends(require_company_admin),
    service: UserService = Depends(get_user_service),
) -> UserResponse:
    """Create a user in the caller's organization."""
    return await service.create_user(principal.org_id, payload, principal)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    payload: UserUpdateRequest,
    principal: Principal = Depends(require_company_admin),
    service: UserService = Depends(get_user_service),
) -> UserResponse:
    """Apply a partial update to a user in the caller's organization."""
    return await service.update_user(principal.org_id, user_id, payload, principal)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_user(
    user_id: UUID,
    principal: Principal = Depends(require_company_admin),
    service: UserService = Depends(get_user_service),
) -> None:
    """Deactivate (soft-delete) a user in the caller's organization."""
    await service.deactivate_user(principal.org_id, user_id, principal)
