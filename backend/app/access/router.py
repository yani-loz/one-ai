"""
Role: Aggregates the access sub-routers into the single access_router main.py includes.
Used by: app.main (include_router).
Depends on: routes.promotion_routes.
Key invariants:
  - Everything under this router is TENANT + PERSON scoped: routes use the access
    dependencies (person-scoped sessions on the oneai_app pool) — never the global engine.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.access.routes.promotion_routes import router as promotion_router

access_router = APIRouter()
access_router.include_router(promotion_router)
