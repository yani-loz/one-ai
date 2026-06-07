"""
Role: The IMAP vendor ADAPTER BOUNDARY — a thin async wrapper over stdlib imaplib that does the
      one thing point 1 needs: connect + authenticate + a NOOP round-trip, then log out. This is
      the seam tests mock (no real server in unit/route tests) and the file to swap if we move to
      aioimaplib in a later point — the connector above it never changes.
Used by: connectors.imap.connector (calls verify_login).
Depends on: imaplib + ssl (stdlib), asyncio (to keep blocking IMAP off the event loop),
            app.connectors.exceptions (ConnectorError base for the two vendor errors).
Key invariants:
  - CONNECTION-ONLY: login -> NOOP -> logout. No mailbox listing, no fetching (that is point 2).
  - BOUNDED: an explicit connect/socket timeout is always set, so a dead host fails fast with
    ImapConnectionError instead of hanging a worker thread (the "must not affect the whole app"
    requirement).
  - The blocking imaplib calls run in asyncio.to_thread so they never block the event loop.
  - The PASSWORD is never placed in an exception message or log. Vendor errors are mapped to
    ImapAuthError (bad credentials) or ImapConnectionError (unreachable/timeout) with generic,
    secret-free messages; the original cause is chained for server-side tracebacks only.
"""

from __future__ import annotations

import asyncio
import imaplib
from typing import Protocol

from app.connectors.exceptions import ConnectorError
from app.connectors.imap.config import ImapConnectionParams

# Short connect/socket timeout — an interactive "test connection" must fail fast, not hang.
_CONNECT_TIMEOUT_SECONDS = 15.0


class ImapAuthError(ConnectorError):
    """IMAP login was rejected (bad username/password). Internal — mapped to a failed check."""


class ImapConnectionError(ConnectorError):
    """The IMAP server was unreachable or timed out. Internal — mapped to a failed check."""


class ImapClient(Protocol):
    """The minimal IMAP capability the connector needs (point 1: verify a login)."""

    async def verify_login(self, params: ImapConnectionParams, secret: str) -> None:
        """Connect + authenticate, then close. Raise ImapAuthError / ImapConnectionError."""
        ...


class DefaultImapClient:
    """Real IMAP client over stdlib imaplib, run in a worker thread with a hard timeout."""

    def __init__(self, timeout_seconds: float = _CONNECT_TIMEOUT_SECONDS) -> None:
        """Configure the connect/socket timeout (seconds)."""
        self._timeout = timeout_seconds

    async def verify_login(self, params: ImapConnectionParams, secret: str) -> None:
        """Run the blocking connect+login+noop+logout off the event loop.

        Raises:
            ImapAuthError: the server rejected the credentials.
            ImapConnectionError: the server was unreachable or timed out.
        """
        await asyncio.to_thread(self._blocking_verify, params, secret)

    def _blocking_verify(self, params: ImapConnectionParams, secret: str) -> None:
        """Blocking connect -> login -> NOOP -> logout. Maps vendor failures to our errors."""
        try:
            client: imaplib.IMAP4 = (
                imaplib.IMAP4_SSL(params.host, params.port, timeout=self._timeout)
                if params.use_ssl
                else imaplib.IMAP4(params.host, params.port, timeout=self._timeout)
            )
        except (OSError, imaplib.IMAP4.error) as exc:
            raise ImapConnectionError("Could not reach the IMAP server.") from exc

        try:
            client.login(params.username, secret)
            client.noop()
        except imaplib.IMAP4.error as exc:  # NB: secret is never included in the message
            raise ImapAuthError("IMAP authentication failed.") from exc
        except OSError as exc:
            raise ImapConnectionError("IMAP connection error during login.") from exc
        finally:
            try:
                client.logout()
            except Exception:  # logout failure must not mask the real outcome
                pass
