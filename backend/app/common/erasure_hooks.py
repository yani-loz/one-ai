"""
Role: Erasure-hook registry — the seam through which feature modules (connectors, entities,
      and future Connect/Ask/Learn stores) join GDPR org erasure WITHOUT app.identity ever
      importing them (CA-CONN-01 / CA-CONN-03: an identity → connectors import would be a
      cycle). Feature modules expose an async hook; the composition root registers it; the
      ErasureService runs every registered hook inside its one erasure transaction.
Used by: app.main (registers each module's hook at startup — explicit calls, no import-side-
         effect magic); app.identity.dependencies (injects the registered hooks into
         ErasureService); app.identity.services.erasure_service (the ErasureHook type).
Depends on: SQLAlchemy AsyncSession (typing only). Leaf module — imports NO feature code.
Key invariants:
  - A hook is `async (org_id, session) -> {table_name: rows_deleted}` and runs INSIDE the
    erasure transaction on the RLS-EXEMPT global session — org-scoping inside each hook's own
    SQL is therefore the ONLY containment. Every statement a hook issues MUST filter by the
    given org_id; the registry cannot enforce that for you.
  - Registration is name-keyed and IDEMPOTENT: re-registering a name replaces the hook (a
    repeated create_app() in tests must not stack duplicate hooks).
  - Iteration order == registration order (dict insertion order). app.main registers hooks
    children-before-referenced-graphs (connectors before entities).
  - Table names returned by different hooks must not collide — each module owns its tables;
    the ErasureService merges the per-hook dicts into one per-table report.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

# One feature module's org-erasure step: delete the org's rows from the module's tables and
# report {table_name: rows_deleted}. MUST org-scope every statement (see module invariants).
ErasureHook = Callable[[UUID, AsyncSession], Awaitable[dict[str, int]]]

# The COMPLETE set of feature modules holding tenant PII — ALL must be registered before any
# erasure runs. An empty-registry guard alone fails open on PARTIAL configuration (a process
# registering only one module would erase incompletely behind a clean certificate — 2026-06-11
# cross-vendor review). Every new PII-holding module joins this tuple in the same commit that
# adds its hook (the FIX_BEFORE_PROD erasure-completeness invariant).
REQUIRED_ERASURE_HOOKS: tuple[str, ...] = ("connectors", "entities")

_hooks: dict[str, ErasureHook] = {}


def register_erasure_hook(name: str, hook: ErasureHook) -> None:
    """Register (or idempotently replace) a feature module's org-erasure hook.

    Contract: `name` identifies the registering module (e.g. "connectors"); re-registering
    the same name replaces the previous hook instead of stacking a duplicate. Called from
    the composition root (app.main.create_app) only — never as an import side effect.
    """
    _hooks[name] = hook


def registered_erasure_hooks() -> dict[str, ErasureHook]:
    """Return a copy of the registered hooks, keyed by module name, in registration order.

    A copy, so callers (ErasureService via dependencies) can never mutate the registry.
    """
    return dict(_hooks)
