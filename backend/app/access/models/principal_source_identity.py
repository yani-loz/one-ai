"""
Role: PrincipalSourceIdentity ORM model — the SECURITY-critical mapping from a per-connector
      external identity (an email address, a login user id, later a Slack user id) to a person,
      with an explicit `verified` flag. Grants resolve ONLY through verified rows (AC8): the
      entity resolver's auto-minted person graph is CONTENT identity and never authorizes.
Used by: the grant writer (recipient/owner → person via verified rows), the login→person binding
      (identity/dependencies resolves the person GUC through the 'auth' namespace), the erasure
      hook (external identifiers are PII → deleted with the tenant).
Depends on: app.common.base_model. FK to person is a composite string reference.
Key invariants:
  - TENANT-SCOPED + RLS (org_isolation) + FORCE, explicit oneai_app grant (0019).
  - UNVERIFIED ⇒ NO GRANT (AC8): the writer skips principals whose only mapping is unverified;
    the login seam resolves NO person GUC through an unverified 'auth' row.
  - ONE mechanism, namespaced by source: source_type='auth' + external_id=str(user_id) is the
    JWT→person login binding (AC20); source_type='email' + external_id=<normalized address> is
    the email-address identity every connector shares. UNIQUE (org_id, source_type, external_id).
  - AC20 (login binding survives entity resolution): rows with source_type='auth' are
    admin/IdP-controlled; the future person-merge tier MUST block any merge that would rewire a
    verified auth binding pending explicit admin confirmation. The merge tier does not exist yet
    (CA-CONN-09) — this invariant is its standing contract, not an implemented guard.
  - person_id FK CASCADE: erasing the person removes the mapping (it is a PII edge).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin

# Identity namespaces: 'auth' = the JWT login binding (external_id = str(user_id));
# 'email' = a normalized email address (shared by every connector that sees addresses).
SOURCE_IDENTITY_TYPES: frozenset[str] = frozenset({"auth", "email"})


class PrincipalSourceIdentity(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """One external identity → person mapping; only `verified` rows ever resolve a grant."""

    __tablename__ = "principal_source_identity"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ({})".format(
                ", ".join(f"'{t}'" for t in sorted(SOURCE_IDENTITY_TYPES))
            ),
            name="ck_principal_source_identity_type",
        ),
        UniqueConstraint(
            "org_id", "source_type", "external_id", name="uq_principal_source_identity"
        ),
        ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name="fk_principal_source_identity_org_id"
        ),
        ForeignKeyConstraint(
            ["org_id", "person_id"],
            ["person.org_id", "person.id"],
            ondelete="CASCADE",
            name="fk_principal_source_identity_person",
        ),
    )

    person_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(postgresql.TEXT, nullable=False)
    # 'auth': str(user_id). 'email': the NORMALIZED address (email_normalizer.normalize_email).
    external_id: Mapped[str] = mapped_column(postgresql.TEXT, nullable=False)
    # UNVERIFIED ⇒ NO GRANT (AC8). Verification is an admin/IdP act, recorded with its actor.
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by_user_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
