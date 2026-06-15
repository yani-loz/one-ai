"""
Role: Enumerations for the connectors module — the connector kinds, auth methods, and
      connection lifecycle states. Single source of these string vocabularies.
Used by: connectors models / schemas / services / base.registry, and the migration's
         CHECK constraints (kept in sync with these values by hand).
Depends on: nothing internal — leaf module.
Key invariants:
  - Values are the canonical strings persisted in the DB and accepted on the wire; the
    migration's CHECK constraints must match these exactly.
  - Point 1 ships a single connector kind (imap) and a single auth method (app_password);
    the enums are the seam where future kinds/methods (e.g. xoauth2) are added.
"""

from __future__ import annotations

from enum import StrEnum


class ConnectorType(StrEnum):
    """A kind of data-source connector. Point 1 ships IMAP only."""

    imap = "imap"


class AuthMethod(StrEnum):
    """How a connection authenticates. Point 1 ships app-password only."""

    app_password = "app_password"


class ConnectionStatus(StrEnum):
    """Lifecycle state of a stored connection.

    configured: saved but not yet verified · connected: last verification succeeded ·
    error: last verification failed (see ConnectorConnection.last_error).
    """

    configured = "configured"
    connected = "connected"
    error = "error"


class OverrideType(StrEnum):
    """A company admin's per-user connector decision (connector_policy_override).

    grant: this user MAY self-connect the type even when org-wide is off (an upgrade) ·
    deny: this user may NOT, even when org-wide is on (an exclusion). Per CO-01 §10.1 a
    per-user override always wins over the org-wide policy; entitlement (Tier 1) is the
    hard ceiling above both.
    """

    grant = "grant"
    deny = "deny"
