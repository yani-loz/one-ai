"""
Role: Pydantic request/response models for the connector endpoints.
Used by: routes.connector_routes, services.connector_service (response building).
Depends on: pydantic, app.connectors.enums, app.connectors.models (for from_model typing).
Key invariants:
  - The response NEVER exposes the secret: no password, no ciphertext, no key version — only the
    non-secret config (host/port/use_ssl), status, and the last (sanitized) error.
  - CreateConnectionRequest is IMAP-shaped for point 1 (the only connector kind); a second
    connector would generalize this into a discriminated union. `password` is write-only (request
    only) and bounded.
  - extra='forbid' on the request rejects unknown fields (no silent typos / smuggled columns).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.connectors.enums import ConnectorType
from app.connectors.models.connector_connection import ConnectorConnection


class CreateConnectionRequest(BaseModel):
    """Configure a new connector connection (point 1: an IMAP mailbox)."""

    model_config = ConfigDict(extra="forbid")

    connector_type: ConnectorType = ConnectorType.imap
    display_name: str = Field(min_length=1, max_length=120)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=993, ge=1, le=65535)
    use_ssl: bool = True
    username: str = Field(min_length=1, max_length=320)
    # Write-only: stored encrypted, never returned. Bounded to a sane credential length.
    password: str = Field(min_length=1, max_length=1024)


class ConnectionResponse(BaseModel):
    """A stored connection as metadata — its params + verification state. NO secret."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    connector_type: str
    display_name: str
    auth_method: str
    username: str
    host: str
    port: int
    use_ssl: bool
    status: str
    last_checked_at: datetime | None
    last_error: str | None
    created_at: datetime

    @classmethod
    def from_model(cls, connection: ConnectorConnection) -> ConnectionResponse:
        """Build a response from the ORM row, unpacking the non-secret config JSONB."""
        config = connection.config or {}
        return cls(
            id=connection.id,
            org_id=connection.org_id,
            connector_type=connection.connector_type,
            display_name=connection.display_name,
            auth_method=connection.auth_method,
            username=connection.username,
            host=str(config.get("host", "")),
            port=int(config.get("port", 0)),  # type: ignore[arg-type]
            use_ssl=bool(config.get("use_ssl", True)),
            status=connection.status,
            last_checked_at=connection.last_checked_at,
            last_error=connection.last_error,
            created_at=connection.created_at,
        )
