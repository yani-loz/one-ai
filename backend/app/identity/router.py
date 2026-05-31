"""
Role: Aggregates the three identity sub-routers (auth, users, platform) into the
      single identity_router that main.py includes.
Used by: app.identity.__init__ (re-export), app.main (include_router).
Depends on: identity.routes.auth_routes / user_routes / platform_routes.
Key invariants:
  - The ONLY place the three identity sub-routers are combined. Routes keep their own
    prefixes (/auth, /users, /platform) so the API stays flat top-level like /health.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.identity.routes.auth_routes import router as auth_router
from app.identity.routes.platform_routes import router as platform_router
from app.identity.routes.user_routes import router as user_router

identity_router = APIRouter()
identity_router.include_router(auth_router)
identity_router.include_router(user_router)
identity_router.include_router(platform_router)
