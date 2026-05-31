"""
Role: Tenant (organization) context propagation — the application-level half of
      tenant isolation (security.md, layer 3). Holds the active org_id for the
      current execution context and exposes it to the data layer.
Used by: app.core.database (scoped_session calls set_current_org + binds the GUC);
         app.identity.dependencies (get_tenant_session, after verifying the JWT).
         get_current_org() is the reserved ambient-read seam — the contextvar is
         published on every tenant session so cross-cutting code (e.g. the future
         audit log) can read the active tenant without threading it through calls.
Depends on: app.core.exceptions. Leaf module otherwise.
Key invariants:
  - Every tenant-scoped DB session is bound to exactly one org_id.
  - `org_id` is the canonical tenant key across the whole system (NOT company_id).
  - org_id is ALWAYS derived from the verified JWT claim — never from a header or
    request body. There is no header fallback seam: an unauthenticated request has
    no tenant and cannot reach a tenant-scoped session.
"""

from __future__ import annotations

from contextvars import ContextVar
from uuid import UUID

from app.core.exceptions import TenantContextMissingError

# Active tenant for the current execution context (async-task-safe).
_current_org_id: ContextVar[UUID | None] = ContextVar("current_org_id", default=None)


def set_current_org(org_id: UUID) -> None:
    """Bind `org_id` as the active tenant for the current context."""
    _current_org_id.set(org_id)


def get_current_org() -> UUID:
    """Return the active tenant, or raise if none is bound.

    The read side of the ambient tenant context (set_current_org publishes it on
    every tenant-scoped session). Reserved for cross-cutting consumers — audit
    logging, background tasks — that need the active org without it being passed in.

    Raises:
        TenantContextMissingError: no tenant has been set in this context.
    """
    org_id = _current_org_id.get()
    if org_id is None:
        raise TenantContextMissingError("No tenant (org_id) bound to the current context.")
    return org_id
