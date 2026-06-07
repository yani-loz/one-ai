"""
Role: IMAP connector package — a self-contained, removable connector module. Point 1 scope:
      connection + authentication only (no fetching/sync).
Used by: connectors.base.registry discovers build_imap_connector by module path; nothing else
         imports this package directly (the abstraction + registry are the only seam).
Depends on: app.connectors.base, app.connectors.enums, imaplib (stdlib, via client).
"""

from __future__ import annotations

from app.connectors.imap.connector import ImapConnector, build_imap_connector

__all__ = ["ImapConnector", "build_imap_connector"]
