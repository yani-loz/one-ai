"""
Role: Aggregates the identity sub-routers (auth, users, platform, + the two break-glass
      support routers) into the single identity_router that main.py includes.
Used by: app.identity.__init__ (re-export), app.main (include_router).
Depends on: identity.routes.auth_routes / user_routes / platform_routes / support_routes.
Key invariants:
  - The ONLY place the identity sub-routers are combined. Routes keep their own prefixes
    (/auth, /users, /platform, /support-access) so the API stays flat top-level like /health.
  - support_routes contributes TWO routers by design: a platform-gated one (/platform/...
    support-requests) and a company-gated one (/support-access) — separate auth domains.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.identity.routes.auth_routes import router as auth_router
from app.identity.routes.erasure_routes import router as erasure_router
from app.identity.routes.platform_routes import router as platform_router
from app.identity.routes.support_routes import (
    company_support_router,
    platform_support_router,
)
from app.identity.routes.user_routes import router as user_router

identity_router = APIRouter()
identity_router.include_router(auth_router)
identity_router.include_router(user_router)
identity_router.include_router(platform_router)
identity_router.include_router(platform_support_router)
identity_router.include_router(company_support_router)
identity_router.include_router(erasure_router)
