"""
Role: Identity & Access module — self-contained auth, user management, and platform
      onboarding for One AI. Public entrypoints: identity_router (mount) and
      register_identity_exception_handlers (HTTP error mapping).
Used by: app.main (mounts the router + registers the handlers).
Depends on: app.core (config/database/tenant/exceptions) ONLY — dependency direction
            is strict: identity -> core, NEVER core -> identity.
Key invariants:
  - Three identity scopes: platform_admin (separate auth domain, global), company_admin
    and member (org-scoped). org_id comes only from the verified JWT.
  - This package is the module's public face; importing internals from outside the
    module is discouraged — go through the router + handlers re-exported here.
"""

from __future__ import annotations

from app.identity.error_handlers import register_identity_exception_handlers
from app.identity.router import identity_router

__all__ = ["identity_router", "register_identity_exception_handlers"]
