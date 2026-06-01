"""
Role: Audit-trail writer + reader. Builds and appends audit_log rows (who/what/when/
      where/why) and serves the platform read views. Holds the audit business logic (A5).
Used by: AuthService / PlatformOrgService / PlatformAuthService (emit events on their own
         session); routes.platform_routes via get_audit_service (reads).
Depends on: app.core.database (SessionLocal — the independent-write session),
            app.core.request_context (IP + request id), repositories.audit_repository,
            schemas.audit_schemas, identity.enums (AuditActorType).
Key invariants:
  - TRANSACTION COUPLING (the core decision):
      * record(): appends on the CALLER'S session, committed WITH the action by the
        request unit-of-work. A successful action can never be silently unlogged; the
        inverse risk — a bad row failing the action — is bounded by building every field
        from already-validated inputs (so a valid action cannot produce an invalid row).
      * record_independently(): appends on its OWN short-lived session, committed at once.
        Used ONLY on a path that RAISES/rolls back (failed or suspended login), where a
        same-session row would be discarded. Best-effort: failures are swallowed + logged
        so an audit hiccup never turns a clean 401/403 into a 500.
  - NO SECRETS: callers pass only metadata; this service never reads a password/hash/token.
  - Reads are newest-first + paginated and return metadata-only entry views.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from app.core.database import SessionLocal
from app.core.request_context import REQUEST_ID_MAX_LENGTH, current_request_context
from app.identity.enums import AuditActorType
from app.identity.models.audit_log import AuditLog
from app.identity.repositories.audit_repository import AuditRepository
from app.identity.schemas.audit_schemas import AuditLogEntryResponse

_logger = logging.getLogger(__name__)


class AuditAction:
    """Canonical action names (dotted namespace) emitted by the platform/auth flows."""

    LOGIN_SUCCESS = "auth.login.success"
    LOGIN_FAILURE = "auth.login.failure"
    LOGIN_BLOCKED = "auth.login.blocked"
    REFRESH = "auth.refresh"
    LOGOUT = "auth.logout"
    ORG_ONBOARD = "org.onboard"
    ORG_SUSPEND = "org.suspend"
    ORG_REACTIVATE = "org.reactivate"
    ORG_STATUS_CHANGE = "org.status_change"
    ORG_LEGAL_HOLD_SET = "org.legal_hold.set"
    ORG_LEGAL_HOLD_CLEAR = "org.legal_hold.clear"
    USER_CREATE = "user.create"
    USER_ROLE_CHANGE = "user.role_change"
    USER_DEACTIVATE = "user.deactivate"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """The metadata of one auditable action (never a secret, never tenant content)."""

    action: str
    actor_type: AuditActorType
    actor_id: UUID | None = None
    actor_email: str | None = None
    org_id: UUID | None = None
    entity_type: str | None = None
    entity_id: UUID | None = None
    details: Mapping[str, object] = field(default_factory=dict)


class AuditService:
    """Append + read the immutable audit trail."""

    def __init__(self, audit: AuditRepository) -> None:
        """Bind the repository (on the caller's session for writes / a plain one for reads)."""
        self._audit = audit

    async def record(self, event: AuditEvent) -> None:
        """Append `event` on the caller's session (same transaction as the action).

        Commits with the request via the unit-of-work, so a successful action is always
        logged atomically with its effect. Use this for events on a path that SUCCEEDS.
        """
        await self._audit.insert(self._build_row(event))

    async def record_independently(self, event: AuditEvent) -> None:
        """Append `event` on its own session, committed immediately (survives a rollback).

        For events emitted on a path that RAISES (failed / suspended login) where the
        request transaction rolls back. Best-effort: any failure is logged and swallowed
        so the audit write can never convert a clean 401/403 into a 500. Opens an extra
        pooled connection per call — under credential-stuffing this compounds the tracked
        bcrypt-DoS / pool-sizing items (docs/FIX_BEFORE_PROD.md).
        """
        try:
            async with SessionLocal() as session:
                session.add(self._build_row(event))
                await session.commit()
        except Exception:  # noqa: BLE001 — audit must never break the primary flow
            _logger.exception("Failed to write independent audit row for %s", event.action)

    @staticmethod
    def _build_row(event: AuditEvent) -> AuditLog:
        """Construct an AuditLog row, stamping IP + request id from the request context.

        request_id is defensively clamped to the column width even though the middleware
        already bounds it — so a non-middleware contextvar setter can never make a same-tx
        INSERT overflow and roll back the action it was recording.
        """
        context = current_request_context()
        request_id = context.request_id[:REQUEST_ID_MAX_LENGTH] if context is not None else None
        return AuditLog(
            actor_type=event.actor_type.value,
            actor_id=event.actor_id,
            actor_email=event.actor_email,
            action=event.action,
            org_id=event.org_id,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            details=dict(event.details),
            ip_address=context.ip_address if context is not None else None,
            request_id=request_id,
        )

    async def list_for_org(
        self, org_id: UUID, *, limit: int, offset: int
    ) -> list[AuditLogEntryResponse]:
        """Return one org's audit trail, newest-first, paginated (metadata only)."""
        rows = await self._audit.list_for_org(org_id, limit=limit, offset=offset)
        return [AuditLogEntryResponse.model_validate(row) for row in rows]

    async def list_global(
        self,
        *,
        action: str | None,
        org_id: UUID | None,
        limit: int,
        offset: int,
    ) -> list[AuditLogEntryResponse]:
        """Return the global audit trail, optionally filtered, newest-first, paginated."""
        rows = await self._audit.list_global(
            action=action, org_id=org_id, limit=limit, offset=offset
        )
        return [AuditLogEntryResponse.model_validate(row) for row in rows]
