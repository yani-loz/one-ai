"""
Role: Pydantic request/response models for break-glass support access (/platform/* + the
      company /support-access endpoints).
Used by: routes.support_routes, the support services.
Depends on: pydantic (external).
Key invariants:
  - SupportGrantResponse is METADATA ONLY — a consent/lifecycle record, never tenant content.
  - `is_active` is COMPUTED by the service (status=='approved' AND now < expires_at), not a
    stored column — the access decision always reads the clock, never a stale status.
  - The reason is bounded (1..500) and is the only caller-supplied free text.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SupportAccessRequest(BaseModel):
    """A platform admin's request for time-boxed break-glass access to one tenant."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)


class SupportGrantResponse(BaseModel):
    """A support-access grant as metadata — its lifecycle state + live active flag."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    requested_by_admin_id: UUID
    requested_by_email: str | None
    reason: str
    status: str
    # Computed live (status='approved' AND now < expires_at); the access source of truth.
    is_active: bool
    decided_at: datetime | None
    decided_by_email: str | None
    expires_at: datetime | None
    created_at: datetime
