"""
Role: Typed, non-secret IMAP connection parameters + a parser from the stored config mapping.
Used by: connectors.imap.client (opens the connection), connectors.imap.connector (builds params
         from the registry's config mapping).
Depends on: nothing internal — leaf module within the imap connector.
Key invariants:
  - Params are NON-SECRET (host/port/use_ssl/username). The password is never modelled here — it
    flows separately as the decrypted secret.
  - Frozen + slotted: a connector's params don't change after construction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_IMAP_PORT = 993


@dataclass(frozen=True, slots=True)
class ImapConnectionParams:
    """Non-secret parameters needed to open an IMAP session."""

    host: str
    port: int
    use_ssl: bool
    username: str


def imap_params_from_config(config: Mapping[str, object]) -> ImapConnectionParams:
    """Build ImapConnectionParams from the registry's config mapping.

    The service passes a mapping that merges the stored `config` JSONB (host/port/use_ssl) with
    the connection's `username` column. Missing/ill-typed values fall back to safe defaults
    (port 993, SSL on) so a malformed row degrades to a failed connection test, not a crash.
    """
    return ImapConnectionParams(
        host=str(config.get("host", "")),
        port=int(config.get("port", DEFAULT_IMAP_PORT)),  # type: ignore[arg-type]
        use_ssl=bool(config.get("use_ssl", True)),
        username=str(config.get("username", "")),
    )
