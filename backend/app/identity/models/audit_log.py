"""
Role: AuditLog ORM model — the append-only action trail (who / what / when / where / why).
      Records METADATA ABOUT ACTIONS, never tenant content.
Used by: AuditRepository (insert + reads); registered on Base.metadata via models/__init__.
Depends on: app.common.base_model (Base, UUIDPrimaryKeyMixin); sqlalchemy.
Key invariants:
  - NOT tenant-scoped: platform/compliance data spanning all orgs (no TenantMixin, no
    org_id NOT NULL). Each row TAGS an affected org_id (nullable) for per-org filtering,
    and the table is deliberately NOT placed under any RLS policy — a future tenant-scoped
    session (user.* events) must still be able to INSERT here (see migration 0005 +
    docs/FIX_BEFORE_PROD.md).
  - NO foreign keys to organizations/users: a row must SURVIVE deletion/rename of the actor
    or org (durable attribution via the denormalized actor_email) — PC-04-AC7 + PC-06 erasure.
  - actor_type is pinned to the AuditActorType enum by a DB CHECK (ck_audit_log_actor_type).
  - APPEND-ONLY against DML: a BEFORE UPDATE OR DELETE trigger raises, so no UPDATE/DELETE
    succeeds even under the current superuser app role (unlike RLS, which a superuser
    bypasses). This is append-only enforcement, NOT true immutability — a superuser can
    still DISABLE TRIGGER; full immutability lands with the least-privilege DB role. The
    trigger is created by migration 0005 and (for create_all in tests) by the after_create
    DDL event below, so both schema paths enforce it.
  - The `details` payload NEVER carries a secret (password/hash/token) or tenant content
    (PC-04-AC4) — the AuditService writer controls every field.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DDL,
    CheckConstraint,
    DateTime,
    Index,
    String,
    event,
    func,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import Base, UUIDPrimaryKeyMixin

# Append-only enforcement. CREATE OR REPLACE on the function makes re-creation idempotent
# across the test fixture's drop/create cycles; the trigger is dropped with the table.
_APPEND_ONLY_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION audit_log_block_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only: % is not permitted', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""

_APPEND_ONLY_TRIGGER_SQL = """
CREATE TRIGGER audit_log_no_mutation
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_block_mutation();
"""


class AuditLog(Base, UUIDPrimaryKeyMixin):
    """One immutable record of a consequential action (append-only against DML)."""

    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('platform_admin', 'user', 'system')",
            name="ck_audit_log_actor_type",
        ),
        Index("ix_audit_log_org_occurred", "org_id", "occurred_at"),
        Index("ix_audit_log_action_occurred", "action", "occurred_at"),
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    actor_email: Mapped[str | None] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    org_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    details: Mapped[dict] = mapped_column(
        postgresql.JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    ip_address: Mapped[str | None] = mapped_column(String(45))
    request_id: Mapped[str | None] = mapped_column(String(64))


# create_all (the fresh-DB test path) builds only table DDL from the model; attach the
# append-only function + trigger so that path enforces immutability identically to
# migration 0005. Registered as TWO events (fired in order) so each runs a single
# statement — asyncpg rejects multiple commands in one execute.
event.listen(AuditLog.__table__, "after_create", DDL(_APPEND_ONLY_FUNCTION_SQL))
event.listen(AuditLog.__table__, "after_create", DDL(_APPEND_ONLY_TRIGGER_SQL))
