"""
Role: Connector-domain exception hierarchy. All inherit app.core.exceptions.OneAIError.
Used by: connectors services / security / base.registry (raise); connectors.error_handlers
         maps each to an HTTP status. Concrete connectors (e.g. imap) subclass ConnectorError
         for their own vendor-specific failures.
Depends on: app.core.exceptions.
Key invariants:
  - Every error here subclasses OneAIError (rule A5 — no bare Exception).
  - Messages NEVER contain a credential (password / token) or a raw vendor error that might.
  - HTTP mapping (set in error_handlers): ConnectionNotFound -> 404; Duplicate -> 409;
    UnknownConnector -> 400; ConnectorConfiguration -> 503. ConnectorSecretError is internal
    (caught by the service during a connection test) and is not mapped to a status.
"""

from __future__ import annotations

from app.core.exceptions import OneAIError


class ConnectorError(OneAIError):
    """Base for all connector-domain errors."""


# — 503 Service Unavailable (server misconfiguration) —
class ConnectorConfigurationError(ConnectorError):
    """The connector subsystem is misconfigured — e.g. an insecure/missing credential key.

    Raised lazily (only when a connector is actually used) so a deployment that uses no
    connectors still boots; surfaces as 503, never a silent 500.
    """


# — 400 Bad Request —
class UnknownConnectorError(ConnectorError):
    """No connector is registered for the requested type (unsupported / not loaded)."""


# — 404 Not Found —
class ConnectionNotFoundError(ConnectorError):
    """No connection with the given id is visible in the caller's org scope (-> 404).

    A company_admin loading another org's connection resolves here — never a cross-org
    existence leak (a 404, not a 403/200-empty).
    """


# — 409 Conflict —
class DuplicateConnectionError(ConnectorError):
    """A connection for this (org, connector_type, username) already exists (-> 409)."""


# — Internal (not HTTP-mapped) —
class ConnectorSecretError(ConnectorError):
    """A stored credential could not be decrypted (corrupt blob or wrong/rotated key).

    Internal: the connection-test path catches this and reports the connection as `error`,
    so a single unreadable credential never crashes a request.
    """
