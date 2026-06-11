"""
Role: ImapConnector — the first concrete BaseConnector. Point 1 scope: verify that an org's
      stored IMAP credentials can connect + authenticate. Plus build_imap_connector, the factory
      the registry registers.
Used by: registered into ConnectorRegistry via build_imap_connector (connectors.base.registry
         discovers it by module path); the service calls verify_connection().
Depends on: app.connectors.base.connector (BaseConnector/ConnectionCheck), app.connectors.enums,
            app.connectors.imap.client (the vendor adapter), app.connectors.imap.config.
Key invariants:
  - SELF-CONTAINED: everything IMAP lives under connectors/imap/. Removing this folder removes
    the connector — the registry then simply doesn't offer 'imap' (failure isolation), and no
    other module imports it directly.
  - verify_connection() NEVER raises for expected failures: it catches the adapter's ImapAuthError
    / ImapConnectionError and returns a ConnectionCheck(ok=False) with a safe, actionable, and
    secret-free message. Only the connection lifecycle is exercised — no fetching (point 2).
  - The client is injectable (default = DefaultImapClient) so tests mock the vendor boundary.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping

from app.connectors.base.connector import BaseConnector, ConnectionCheck
from app.connectors.base.incremental_fetch import FetchBatch, FolderCursor
from app.connectors.enums import ConnectorType
from app.connectors.imap.client import (
    DefaultImapClient,
    ImapAuthError,
    ImapClient,
    ImapConnectionError,
)
from app.connectors.imap.config import ImapConnectionParams, imap_params_from_config
from app.connectors.imap.sync.imap_fetcher import ImapIncrementalFetcher, SessionOpener


class ImapConnector(BaseConnector):
    """IMAP mailbox connector — verifies a login (point 1) AND fetches incrementally (SyncRunner).

    Implements SupportsIncrementalFetch (count_pending / fetch_incremental) so the runner can pull
    mail via the Protocol + the registry, without importing this module.
    """

    def __init__(
        self,
        params: ImapConnectionParams,
        secret: str,
        client: ImapClient | None = None,
        session_opener: SessionOpener | None = None,
    ) -> None:
        """Hold the params + decrypted secret; default to the real client + session opener."""
        self._params = params
        self._secret = secret
        self._client = client or DefaultImapClient()
        self._session_opener = session_opener

    @property
    def connector_type(self) -> ConnectorType:
        """This connector handles IMAP mailboxes."""
        return ConnectorType.imap

    def _fetcher(self) -> ImapIncrementalFetcher:
        """Build a fresh incremental fetcher (each opens + closes its own IMAP session)."""
        return ImapIncrementalFetcher(self._params, self._secret, self._session_opener)

    async def count_pending(self, cursors: Mapping[str, FolderCursor]) -> int:
        """SupportsIncrementalFetch: count NEW messages across folders since the cursors."""
        return await self._fetcher().count_pending(cursors)

    def fetch_incremental(self, cursors: Mapping[str, FolderCursor]) -> AsyncIterator[FetchBatch]:
        """SupportsIncrementalFetch: stream byte-bounded batches of NEW mail since the cursors."""
        return self._fetcher().fetch_incremental(cursors)

    async def verify_connection(self) -> ConnectionCheck:
        """Attempt a login round-trip; report the outcome without raising or leaking the secret."""
        try:
            await self._client.verify_login(self._params, self._secret)
        except ImapAuthError:
            return ConnectionCheck(
                ok=False,
                message="Authentication failed — check the username and password.",
            )
        except ImapConnectionError:
            return ConnectionCheck(
                ok=False,
                message="Could not reach the mailbox server — check the host, port, and SSL "
                "setting.",
            )
        return ConnectionCheck(ok=True, message="Connection verified.")


def build_imap_connector(config: Mapping[str, object], secret: str) -> BaseConnector:
    """Registry factory: build an ImapConnector from a config mapping + decrypted secret.

    The config mapping carries host/port/use_ssl (from the stored JSONB) and username (merged in
    by the service). The real IMAP client is used; tests register a factory with a fake client.
    """
    return ImapConnector(imap_params_from_config(config), secret)
