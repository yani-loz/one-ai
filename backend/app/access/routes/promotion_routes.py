"""
Role: The one-click promotion endpoint (PF-01 AC13) — a content OWNER widens one restricted
      object to org visibility; atomic (audit + lineage + flip in the request transaction).
Used by: app.access.router (aggregated into main).
Depends on: app.identity.dependencies (Principal + get_tenant_session — promotion is a WRITE
      flow, so it runs on the app plane; the SELECT-only reader pool cannot flip a scope),
      .services.promotion_service.
Key invariants:
  - Routes parse + delegate + map errors (≤20 lines of logic each, rule A5): ownership and
    lineage rules live in PromotionService and the DB triggers, never here.
  - Cross-tenant / unknown object → 404 (never a 200-empty, never an existence-leaking body);
    same-org non-owner → 403 (the app plane sees the row; ownership is the authz, exactly
    like the CO-01 admin plane).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.exceptions import ContentObjectNotFoundError, PromotionNotAllowedError
from app.access.services.promotion_service import PromotionService
from app.identity.dependencies import get_current_principal, get_tenant_session
from app.identity.principal import Principal
from app.identity.repositories.audit_repository import AuditRepository
from app.identity.services.audit_service import AuditService

router = APIRouter(prefix="/access/promotions", tags=["access"])


class PromotionRequest(BaseModel):
    """One promotion approval: which object to widen to org visibility."""

    object_type: str = Field(pattern="^email_message$")
    object_id: UUID


class PromotionResponse(BaseModel):
    """The recorded widening: the append-only lineage row's id."""

    promotion_id: UUID


@router.post("", status_code=status.HTTP_201_CREATED, response_model=PromotionResponse)
async def approve_promotion(
    body: PromotionRequest,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_tenant_session),
) -> PromotionResponse:
    """Widen one restricted object to org visibility (owner-only, audited, atomic)."""
    service = PromotionService(session, AuditService(AuditRepository(session)))
    try:
        promotion_id = await service.promote_email_message(
            org_id=principal.org_id,  # type: ignore[arg-type]  # non-None: enforced by the session dependency
            approver_user_id=principal.subject_id,
            approver_email=None,
            message_id=body.object_id,
        )
    except ContentObjectNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Content object not found.") from error
    except PromotionNotAllowedError as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(error)) from error
    return PromotionResponse(promotion_id=promotion_id)
