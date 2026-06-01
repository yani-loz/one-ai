"""
Role: SupportGrant ORM model — one break-glass support-access request and its lifecycle.
      The record IS the consent artifact: a platform admin requests time-boxed access to a
      tenant, and a company_admin of that tenant must approve it. Metadata only.
Used by: SupportGrantRepository; registered on Base.metadata via models/__init__.
Depends on: app.common.base_model (Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin).
Key invariants:
  - TENANT-TAGGED (TenantMixin org_id, NOT NULL + indexed): a grant always targets one
    tenant. The company side reads it org-scoped (a company_admin sees ONLY their org's
    grants); the platform side spans orgs on a plain session (the documented cross-org
    exception, same as login/onboard). RLS-ready, inert today.
  - NO foreign keys (durable attribution via denormalized emails, like audit_log): the row
    survives deletion/rename of the requester, decider, or org — it is a compliance record.
  - `status` is pinned by a DB CHECK to requested|approved|denied|revoked. There is NO
    stored `expired` state: expiry is computed LIVE against `expires_at` (a grant is active
    iff status='approved' AND now < expires_at), so a clock never disagrees with a column.
  - Only the company-approval path may set `status='approved'` (consent) and `expires_at`
    (the time box). No platform path can self-approve — enforced in the services.
  - created_at (TimestampMixin) IS the requested-at time; updated_at moves on each transition.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class SupportGrant(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """A break-glass support-access request + its approval/expiry lifecycle (metadata only)."""

    __tablename__ = "support_grant"
    __table_args__ = (
        CheckConstraint(
            "status IN ('requested', 'approved', 'denied', 'revoked')",
            name="ck_support_grant_status",
        ),
    )

    requested_by_admin_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=False
    )
    # Denormalized so the approving company_admin sees WHO is asking (informed consent),
    # and the record stays attributable after the admin is deleted/renamed.
    requested_by_email: Mapped[str | None] = mapped_column(String(320))
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="requested"
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_user_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    decided_by_email: Mapped[str | None] = mapped_column(String(320))
    # Set only on approval (now + window). Expiry is computed against this, never stored.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
