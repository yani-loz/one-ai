"""
Role: FactProvenance ORM model — the fail-closed anchor every future Learn-layer fact must hang
      from: which source objects justify the fact, and whether that justification still stands.
      SCHEMA SHIPS NOW (PF-01) so the Learn layer structurally cannot ship facts un-anchored;
      population is Step 7's job (AC6's tests run on synthetic rows until then).
Used by: (future) the Learn layer's fact writer + the fact visibility policy; the erasure hook
      (provenance rows die with the facts they anchor); quarantine flips on provenance shrink.
Depends on: app.common.base_model.
Key invariants:
  - TENANT-SCOPED + RLS (org_isolation) + FORCE, explicit oneai_app grant (0019).
  - FAIL-CLOSED CONTRACT (AC6): a fact without a live ('active') provenance row is unreadable;
    any provenance shrink (source erased, grant revoked, scope suspended) flips
    status='quarantined' and the fact stops resolving. Enforced by the future fact table's
    policy; the vocabulary and shape are pinned HERE so that policy has one thing to join.
  - fact_id is a bare UUID (no FK): the fact table does not exist yet. The FK lands with it —
    tracked in the epic's AC22-style failing placeholder, not silently forgotten.
  - Audience rule for derived facts (cross-vendor amendment 4): single-source facts inherit the
    source audience; conjunctive multi-source facts take the INTERSECTION; anything ambiguous
    stays quarantined. That rule lives in the future writer — this table records the sources.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin

FACT_PROVENANCE_STATUSES: frozenset[str] = frozenset({"active", "quarantined"})


class FactProvenance(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """One (future fact → source object) anchor; quarantined when the justification shrinks."""

    __tablename__ = "fact_provenance"
    __table_args__ = (
        CheckConstraint(
            "status IN ({})".format(", ".join(f"'{s}'" for s in sorted(FACT_PROVENANCE_STATUSES))),
            name="ck_fact_provenance_status",
        ),
        ForeignKeyConstraint(["org_id"], ["organizations.id"], name="fk_fact_provenance_org_id"),
        Index("ix_fact_provenance_fact", "org_id", "fact_id"),
        Index("ix_fact_provenance_source", "org_id", "source_object_type", "source_object_id"),
    )

    # The future Learn-layer fact this row anchors (no FK until that table exists).
    fact_id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), nullable=False)
    source_object_type: Mapped[str] = mapped_column(postgresql.TEXT, nullable=False)
    source_object_id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(postgresql.TEXT, nullable=False, server_default="active")
