"""
Role: The connector ABSTRACTION — the BaseConnector ABC every concrete connector implements,
      plus ConnectionCheck, the result of verifying a connection. This is the seam that keeps
      connectors decoupled: the rest of the app depends ONLY on this + the registry, never on a
      concrete connector module.
Used by: concrete connectors (e.g. connectors.imap.connector); connectors.base.registry
         (stores factories that build these); connectors.services.connector_service (calls
         verify_connection).
Depends on: app.connectors.enums (ConnectorType) — leaf within the connectors module.
Key invariants:
  - Point 1 contract is connection-only: a connector exposes its `connector_type` and a single
    `verify_connection()`. The held-session lifecycle (open/keep/close) + sync methods land in a
    later point, when fetching actually needs them — no dead stubs now.
  - verify_connection() MUST NOT raise for expected failures (auth wrong, host unreachable,
    timeout): it returns ConnectionCheck(ok=False, <safe message>). This is what makes a bad
    connection a clean result instead of a 500 — the failure-isolation requirement.
  - A connector is self-contained and stateless across calls: it holds its own params/secret and
    opens/closes any transport entirely within verify_connection().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.connectors.enums import ConnectorType


@dataclass(frozen=True, slots=True)
class ConnectionCheck:
    """Outcome of verifying a connection.

    Attributes:
        ok: True iff the connector reached the source and authenticated.
        message: a short, human-readable, SECRET-FREE explanation (shown to the admin and, on
            failure, stored in ConnectorConnection.last_error).
    """

    ok: bool
    message: str


class BaseConnector(ABC):
    """Abstract base every data-source connector implements (point 1: connection only)."""

    @property
    @abstractmethod
    def connector_type(self) -> ConnectorType:
        """The kind of connector this is (e.g. ConnectorType.imap)."""

    @abstractmethod
    async def verify_connection(self) -> ConnectionCheck:
        """Connect + authenticate against the source, then close, reporting the outcome.

        Contract: returns ConnectionCheck(ok=True) on a successful authenticated round-trip;
        ConnectionCheck(ok=False, <safe message>) for any expected failure (bad credentials,
        unreachable host, timeout). Never raises for these — the message must not leak the
        secret. Must complete in bounded time (the implementation sets a connect timeout).
        """
