"""
Role: RefreshToken ORM model — server-side record of an issued opaque refresh token.
Used by: RefreshTokenRepository; the auth/platform services rotate + revoke these.
Depends on: app.common.base_model (Base, UUIDPrimaryKeyMixin, TimestampMixin).
Key invariants:
  - token_hash stores ONLY the sha256 hex of the opaque token — the raw token is
    never persisted, logged, or returned after issuance.
  - subject_type is constrained to ('user','platform_admin') by a DB CHECK, matching
    SubjectType. subject_id + subject_type together identify the owner across the
    two auth domains (users and platform_admins live in separate tables, so no FK).
  - A token is usable iff revoked_at IS NULL AND expires_at > now(). Rotation sets
    revoked_at on the presented token before issuing a new pair.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import Base, TimestampMixin, UUIDPrimaryKeyMixin


class RefreshToken(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Persisted hash of an opaque refresh token, with rotation/revocation state."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        CheckConstraint(
            "subject_type IN ('user', 'platform_admin')",
            name="ck_refresh_tokens_subject_type",
        ),
    )

    subject_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=False, index=True
    )
    subject_type: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
