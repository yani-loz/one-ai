"""
Role: Identity model package — imports all four ORM models so Alembic autogenerate
      and Base.metadata see the full identity schema.
Used by: app.db.migrations.env (metadata completeness); identity repositories.
Depends on: the four model modules in this package.
Key invariants:
  - Importing this package registers every identity table on Base.metadata.
"""

from __future__ import annotations

from app.identity.models.audit_log import AuditLog
from app.identity.models.organization import Organization
from app.identity.models.platform_admin import PlatformAdmin
from app.identity.models.refresh_token import RefreshToken
from app.identity.models.support_grant import SupportGrant
from app.identity.models.user import User

__all__ = [
    "AuditLog",
    "Organization",
    "PlatformAdmin",
    "RefreshToken",
    "SupportGrant",
    "User",
]
