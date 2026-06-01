"""
Role: Pydantic response model for audit-log read endpoints (/platform/*/audit).
Used by: routes.platform_routes (serialization), services.audit_service (list views).
Depends on: pydantic (external).
Key invariants:
  - METADATA ONLY: an audit entry exposes who/what/when/where about an ACTION — never
    tenant content and never a secret (the writer guarantees `details` carries neither).
  - Mirrors the AuditLog columns 1:1 (from_attributes) so the read path adds no logic.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

# Pagination bounds shared by the audit read endpoints.
DEFAULT_AUDIT_PAGE_SIZE = 50
MAX_AUDIT_PAGE_SIZE = 200


class AuditLogEntryResponse(BaseModel):
    """One audit-trail entry as metadata (no tenant content, no secrets)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    occurred_at: datetime
    actor_type: str
    actor_id: UUID | None
    actor_email: str | None
    action: str
    org_id: UUID | None
    entity_type: str | None
    entity_id: UUID | None
    details: dict
    ip_address: str | None
    request_id: str | None
