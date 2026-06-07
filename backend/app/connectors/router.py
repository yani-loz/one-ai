"""
Role: Aggregates the connectors sub-routers into the single connectors_router that main.py
      includes.
Used by: app.connectors.__init__ (re-export), app.main (include_router).
Depends on: app.connectors.routes.connector_routes.
Key invariants:
  - The ONLY place connector sub-routers are combined; routes keep their own prefix
    (/connectors) so the API stays flat top-level like /health and the identity routers.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.connectors.routes.connector_routes import router as connector_router

connectors_router = APIRouter()
connectors_router.include_router(connector_router)
