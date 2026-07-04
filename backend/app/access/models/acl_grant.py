"""
Role: AclGrant ORM model — one principal's right to retrieve one content object (PF-01's core
      table). The RESTRICTIVE `visibility` RLS policies on content tables EXISTS-join this table:
      a restricted row is visible iff a live (revoked_at IS NULL) grant matches the session's
      app.current_person_id GUC.
Used by: the content-table visibility policies (migration 0019, SQL-side), the grant writer
      (app.access.services.grant_writer), revocation/reconciliation, the erasure hook.
Depends on: app.common.base_model. FKs to person / connector_connection are composite
      (org_id, id) string references — no Python import of those models (same-Base registration).
Key invariants:
  - TENANT-SCOPED + RLS (org_isolation) + FORCE, explicit oneai_app grant (0019).
  - REVOCATION IS A TOMBSTONE: revoked_at set = the grant stops matching IMMEDIATELY (the policy
    filters revoked_at IS NULL); the row is kept as the works-council trail until erasure. The
    partial UNIQUE (org_id, object_type, object_id, person_id) WHERE revoked_at IS NULL keeps
    re-grant after revocation clean (AC14/AC21).
  - UNKNOWN ⇒ DENY lives in the WRITER, not here: a grant row only ever exists for a principal
    resolved through a VERIFIED principal_source_identity (AC8).
  - person_id FK CASCADE: erasing a person hard-deletes their grants (grants are PII edges);
    connection_id FK CASCADE: deleting a connection purges the grants it produced.
  - provenance is CHECK-pinned; 'admin_widen' is deliberately NOT a value — audience widening
    exists only as visibility_promotion (AC5).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, func, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin

# Object types a grant can attach to. 'email_message' = per-message email grants (PF-01 email
# capture); 'mailbox' = container grants on a connection (reserved for shared-mailbox/Slack-style
# container capture — no writer emits it yet).
GRANT_OBJECT_TYPES: frozenset[str] = frozenset({"email_message", "mailbox"})

# How the grant came to exist. NEVER 'admin_widen' — widening is visibility_promotion only (AC5).
GRANT_PROVENANCES: frozenset[str] = frozenset({"recipient", "sender", "owner"})


class AclGrant(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """One (principal → content object) retrieval right, tombstoned on revocation."""

    __tablename__ = "acl_grant"
    __table_args__ = (
        CheckConstraint(
            "object_type IN ({})".format(", ".join(f"'{t}'" for t in sorted(GRANT_OBJECT_TYPES))),
            name="ck_acl_grant_object_type",
        ),
        CheckConstraint(
            "provenance IN ({})".format(", ".join(f"'{p}'" for p in sorted(GRANT_PROVENANCES))),
            name="ck_acl_grant_provenance",
        ),
        ForeignKeyConstraint(["org_id"], ["organizations.id"], name="fk_acl_grant_org_id"),
        ForeignKeyConstraint(
            ["org_id", "person_id"],
            ["person.org_id", "person.id"],
            ondelete="CASCADE",
            name="fk_acl_grant_person_id_org",
        ),
        ForeignKeyConstraint(
            ["org_id", "connection_id"],
            ["connector_connection.org_id", "connector_connection.id"],
            ondelete="CASCADE",
            name="fk_acl_grant_connection_id_org",
        ),
        # One LIVE grant per (object, principal); tombstones don't block a clean re-grant.
        Index(
            "uq_acl_grant_live",
            "org_id",
            "object_type",
            "object_id",
            "person_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        # The visibility-policy lookup path: person → objects (partial: live grants only).
        Index(
            "ix_acl_grant_person_object_live",
            "org_id",
            "person_id",
            "object_type",
            "object_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    person_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=False, index=True
    )
    object_type: Mapped[str] = mapped_column(postgresql.TEXT, nullable=False)
    object_id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), nullable=False)
    # The connection whose sync produced this grant (reconciliation scope + lifecycle cascade).
    connection_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    provenance: Mapped[str] = mapped_column(postgresql.TEXT, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Tombstone (AC14/AC21): set = the grant stops matching immediately; row retained for audit.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
