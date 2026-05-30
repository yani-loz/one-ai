"""
Role: Tenant (organization) context propagation — the application-level half of
      tenant isolation (security.md, layer 3). Resolves the active org_id for a
      request and exposes it to the data layer.
Used by: app.core.database.get_tenant_session; future tenant-scoped routes/services.
Depends on: app.core.config, app.core.exceptions.
Key invariants:
  - Every tenant-scoped DB session is bound to exactly one org_id.
  - `org_id` is the canonical tenant key across the whole system (NOT company_id).
  - In production, a request with no resolvable tenant is REFUSED, never defaulted.
"""

from __future__ import annotations

from contextvars import ContextVar
from uuid import UUID

from fastapi import Header

from app.core.config import get_settings
from app.core.exceptions import TenantContextMissingError

# Active tenant for the current execution context (async-task-safe).
_current_org_id: ContextVar[UUID | None] = ContextVar("current_org_id", default=None)


def set_current_org(org_id: UUID) -> None:
    """Bind `org_id` as the active tenant for the current context."""
    _current_org_id.set(org_id)


def get_current_org() -> UUID:
    """Return the active tenant, or raise if none is bound.

    Raises:
        TenantContextMissingError: no tenant has been set in this context.
    """
    org_id = _current_org_id.get()
    if org_id is None:
        raise TenantContextMissingError("No tenant (org_id) bound to the current context.")
    return org_id


async def resolve_org_id(x_org_id: str | None = Header(default=None)) -> UUID:
    """FastAPI dependency: resolve the request's tenant from the `X-Org-Id` header.

    Contract:
        - header present -> the parsed UUID is used;
        - header absent  -> local/dev falls back to settings.default_org_id, while
                            production raises TenantContextMissingError.

    NOTE: this is a scaffold seam. Phase 4 replaces the header with a verified JWT
    claim; nothing downstream in the data layer changes.
    """
    settings = get_settings()
    raw = x_org_id or (None if settings.is_production else settings.default_org_id)
    if raw is None:
        raise TenantContextMissingError("Missing X-Org-Id and no fallback allowed in production.")
    try:
        return UUID(raw)
    except ValueError as exc:
        raise TenantContextMissingError(f"Invalid org_id: {raw!r}") from exc
