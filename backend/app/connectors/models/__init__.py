"""
Role: Connector model package — imports every connector ORM model so it registers on
      Base.metadata (for Alembic autogenerate + the test schema fixture).
Used by: app.db.migrations.env (imported for target metadata), connectors repositories, tests.
Depends on: app.connectors.models.connector_connection.
Key invariants:
  - Importing this package registers every connector table on Base.metadata — relied on by
    db.migrations.env (autogenerate), the test schema fixtures, AND the standing RLS-invariant
    test (which enumerates TenantMixin subclasses and would miss a table whose model isn't
    imported).
"""

from __future__ import annotations

from app.connectors.models.connector_connection import ConnectorConnection
from app.connectors.models.connector_sync_cursor import ConnectorSyncCursor
from app.connectors.models.connector_sync_run import ConnectorSyncRun

__all__ = ["ConnectorConnection", "ConnectorSyncCursor", "ConnectorSyncRun"]
