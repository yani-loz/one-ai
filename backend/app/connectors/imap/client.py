"""
Role: The IMAP vendor ADAPTER BOUNDARY — a thin async wrapper over stdlib imaplib that does the
      one thing point 1 needs: connect + authenticate + a NOOP round-trip, then log out. Also owns
      the shared transport-security helper (upgrade_to_starttls) the fetch path reuses. This is
      the seam tests mock (no real server in unit/route tests) and the file to swap if we move to
      aioimaplib in a later point — the connector above it never changes.
Used by: connectors.imap.connector (calls verify_login); connectors.imap.fetch_session
         (upgrade_to_starttls + the vendor error types).
Depends on: imaplib + ssl (stdlib), asyncio (to keep blocking IMAP off the event loop),
            app.connectors.exceptions (ConnectorError base for the vendor errors),
            app.connectors.imap.egress_guard (resolve-then-validate-then-dial SSRF defence).
Key invariants:
  - CONNECTION-ONLY: login -> NOOP -> logout. No mailbox listing, no fetching (that is point 2).
  - BOUNDED: an explicit connect/socket timeout is always set, so a dead host fails fast with
    ImapConnectionError instead of hanging a worker thread (the "must not affect the whole app"
    requirement). Name resolution is bounded SEPARATELY by the egress guard (the socket timeout
    does not cover getaddrinfo).
  - SSRF-SAFE: the host is resolved + validated by egress_guard BEFORE connecting, and the connect
    is pinned to a validated IP (rebind-proof), so an authenticated tenant admin cannot turn this
    server-side dial into an internal-network probe (incl. cloud metadata 169.254.169.254). A
    blocked target raises EgressBlockedError, surfaced as the generic connection failure.
  - The blocking imaplib calls run in asyncio.to_thread so they never block the event loop.
  - TRANSPORT SECURITY: the SSL path always passes ssl.create_default_context() (certificate +
    hostname verification — never imaplib's unverified default); the non-SSL path MUST upgrade
    via STARTTLS before LOGIN and refuses with ImapTlsUnavailableError if the server cannot do
    TLS. Credentials are NEVER sent over an unencrypted socket.
  - The PASSWORD is never placed in an exception message or log. Vendor errors are mapped to
    ImapAuthError (bad credentials) or ImapConnectionError (unreachable/timeout) with generic,
    secret-free messages; the original cause is chained for server-side tracebacks only.
"""

from __future__ import annotations

import asyncio
import imaplib
import ssl
from typing import Protocol

from app.connectors.exceptions import ConnectorError
from app.connectors.imap.config import ImapConnectionParams
from app.connectors.imap.egress_guard import (
    EgressBlockedError,
    open_guarded_imap4,
    open_guarded_imap4_ssl,
)

# Short connect/socket timeout — an interactive "test connection" must fail fast, not hang.
_CONNECT_TIMEOUT_SECONDS = 15.0


class ImapAuthError(ConnectorError):
    """IMAP login was rejected (bad username/password). Internal — mapped to a failed check."""


class ImapConnectionError(ConnectorError):
    """The IMAP server was unreachable or timed out. Internal — mapped to a failed check."""


class ImapTlsUnavailableError(ImapConnectionError):
    """A non-SSL server offered no working STARTTLS — plaintext LOGIN is refused.

    Subclasses ImapConnectionError so every existing connection-failure handler (connector
    verify_connection, sync runner) treats it as a failed connection, never an unhandled 500.
    """


def _shutdown_quietly(client: imaplib.IMAP4) -> None:
    """Best-effort low-level socket close (no LOGOUT round-trip); never raises."""
    try:
        client.shutdown()
    except Exception:
        pass


def upgrade_to_starttls(client: imaplib.IMAP4) -> None:
    """Upgrade a cleartext IMAP connection to TLS, or close it and refuse — never plaintext LOGIN.

    Contract: call immediately after connecting a non-SSL (`use_ssl=false`) client, BEFORE login.
    Uses a default verifying ssl context (certificate + hostname checks). On ANY failure the
    socket is closed first, so callers never hold (or LOGIN over) a half-secured connection.

    Raises:
        ImapTlsUnavailableError: the server does not advertise/accept STARTTLS.
        ImapConnectionError: the TLS handshake itself failed (e.g. an invalid certificate).
    """
    try:
        client.starttls(ssl_context=ssl.create_default_context())
    except imaplib.IMAP4.error as exc:
        _shutdown_quietly(client)
        raise ImapTlsUnavailableError(
            "The IMAP server does not support STARTTLS; refusing to send credentials over an "
            "unencrypted connection."
        ) from exc
    except OSError as exc:
        _shutdown_quietly(client)
        raise ImapConnectionError(
            "Could not establish a TLS connection to the IMAP server."
        ) from exc


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
        """Blocking resolve+validate -> connect -> (STARTTLS if non-SSL) -> login -> NOOP -> logout.

        Maps vendor failures to our errors. The host is SSRF-validated first (egress_guard) and the
        connect is pinned to a validated IP — a loopback/private/link-local/metadata target is
        refused before any socket opens. The SSL path verifies the server certificate against the
        ORIGINAL hostname (default ssl context, rebind-proof IP dial); the non-SSL path is upgraded
        via STARTTLS or refused — the password is never transmitted over an unencrypted socket.
        """
        # Resolve + validate then dial the validated IP (defeats DNS-rebind); the openers own the
        # on/off switch. A blocked target (EgressBlockedError) becomes a generic connection failure
        # — it never reveals which rule matched, so the verify outcome can't aid an internal probe.
        try:
            client: imaplib.IMAP4 = (
                open_guarded_imap4_ssl(params, self._timeout, ssl.create_default_context())
                if params.use_ssl
                else open_guarded_imap4(params, self._timeout)
            )
        except EgressBlockedError as exc:
            raise ImapConnectionError("Could not reach the IMAP server.") from exc
        except (OSError, imaplib.IMAP4.error) as exc:
            raise ImapConnectionError("Could not reach the IMAP server.") from exc

        if not params.use_ssl:
            upgrade_to_starttls(client)  # closes the socket + raises if TLS cannot be established

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
