"""
Role: VisibilityPromotion ORM model — the append-only record of one audience WIDENING: a
      restricted-origin content object flipped to org visibility, by whom, anchored to which
      audit row. This is THE ONLY widening path (PF-01 §6: 'admin_widen' was removed from the
      grant vocabulary) and the works-council artifact: "show me every time something private
      became org-visible, who approved it, and when" is one SELECT.
Used by: the promotion service (writes, atomically with the scope flip + audit row); the
      DB lineage-guard trigger (migration 0019) which REJECTS any restricted-origin row reaching
      visibility_scope='org' without a matching row here (AC5, INSERT and UPDATE alike).
Depends on: app.common.base_model. FKs to users / audit_log are string references.
Key invariants:
  - TENANT-SCOPED + RLS (org_isolation) + FORCE, explicit oneai_app grant (0019).
  - APPEND-ONLY for UPDATE: a DB trigger rejects UPDATEs (history cannot be rewritten). DELETE
    stays allowed for the GDPR erasure hook only — the lineage survives in the retained
    append-only audit_log (Art. 17(3) basis, same honesty model as PC-06).
  - approved_by_user_id and audit_log_id are NOT NULL by schema: an unattributed or un-audited
    widening is unrepresentable (AC5, PF-01-S3).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class VisibilityPromotion(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """One approved audience widening (restricted → org), human-attributed and audit-anchored."""

    __tablename__ = "visibility_promotion"
    __table_args__ = (
        CheckConstraint(
            "from_scope = 'restricted' AND to_scope = 'org'",
            name="ck_visibility_promotion_direction",
        ),
        ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name="fk_visibility_promotion_org_id"
        ),
        ForeignKeyConstraint(
            ["org_id", "approved_by_user_id"],
            ["users.org_id", "users.id"],
            name="fk_visibility_promotion_approver",
        ),
        ForeignKeyConstraint(
            ["audit_log_id"], ["audit_log.id"], name="fk_visibility_promotion_audit"
        ),
        # The lineage-guard trigger's lookup: does THIS object have promotion lineage?
        Index("ix_visibility_promotion_object", "org_id", "object_type", "object_id"),
    )

    object_type: Mapped[str] = mapped_column(postgresql.TEXT, nullable=False)
    object_id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), nullable=False)
    from_scope: Mapped[str] = mapped_column(
        postgresql.TEXT, nullable=False, server_default="restricted"
    )
    to_scope: Mapped[str] = mapped_column(postgresql.TEXT, nullable=False, server_default="org")
    # The HUMAN who approved (PF-01-S3): never NULL, never a system id.
    approved_by_user_id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), nullable=False)
    # The audit_log row written in the same transaction: the immutable half of the lineage.
    audit_log_id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), nullable=False)
