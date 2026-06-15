"""
Role: Aggregates the connectors sub-routers into the single connectors_router that main.py
      includes — the CO-01 three tiers + the admin connection lifecycle.
Used by: app.connectors.__init__ (re-export), app.main (include_router).
Depends on: routes.connector_routes (Tier-2 admin CRUD, /admin/connectors),
            routes.connector_governance_routes (Tier-2 policies, /admin/connectors),
            routes.me_connector_routes (Tier-3, /me/connectors),
            routes.connector_entitlement_routes (Tier-1, /platform/orgs/{id}/...).
Key invariants:
  - ORDER MATTERS: the governance router (literal /admin/connectors/governance|policies|overrides)
    is included BEFORE the admin CRUD router (which owns /admin/connectors/{connection_id}); a
    request to /admin/connectors/policies must match the literal route, not be parsed as a UUID
    connection id. Starlette resolves in registration order, so governance is registered first.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.connectors.routes.connector_entitlement_routes import router as entitlement_router
from app.connectors.routes.connector_governance_routes import router as governance_router
from app.connectors.routes.connector_routes import router as connector_router
from app.connectors.routes.me_connector_routes import router as me_connector_router

connectors_router = APIRouter()
# Governance (literal paths) BEFORE the admin CRUD router (/{connection_id}) — see module invariant.
connectors_router.include_router(governance_router)
connectors_router.include_router(connector_router)
connectors_router.include_router(me_connector_router)
connectors_router.include_router(entitlement_router)
