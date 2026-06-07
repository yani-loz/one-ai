"""
Role: Connectors module — self-contained data-source connectors for One AI. Point 1 scope:
      connection + authentication (no fetching/sync). Public entrypoints: connectors_router
      (mount) and register_connector_exception_handlers (HTTP error mapping).
Used by: app.main (mounts the router + registers the handlers).
Depends on: app.core (config/database/exceptions) and app.identity (the auth seam:
            require_company_admin + get_tenant_session). NEVER imported by app.core.
Key invariants:
  - Each CONNECTOR (imap, and future kinds) is a self-contained, removable folder under this
    package; the rest of the app depends only on connectors.base (the abstraction) + the registry,
    so adding/removing a connector never affects other modules (failure isolation).
  - org_id comes only from the verified JWT (via identity.get_tenant_session). Credentials are
    encrypted at rest (connectors.security.credential_cipher).
"""

from __future__ import annotations

from app.connectors.error_handlers import register_connector_exception_handlers
from app.connectors.router import connectors_router

__all__ = ["connectors_router", "register_connector_exception_handlers"]
