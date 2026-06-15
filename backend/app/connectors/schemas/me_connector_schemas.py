"""
Role: Pydantic models for the Tier-3 self-connect plane (CO-01 /me/connectors) — the connector
      types a user is allowed to connect, and the self-connect request (mailbox params + the
      GDPR Art. 7 consent captured at connect time). The connection VIEW reuses ConnectionResponse.
Used by: routes.me_connector_routes, services.me_connector_service.
Depends on: pydantic, app.connectors.enums.
Key invariants:
  - The owner sees their OWN connection params (ConnectionResponse) — that's their mailbox, not a
    §7 violation. Admins/platform get the metadata-only governance view instead.
  - consent.accepted MUST be true for a self-connect to proceed (the HITL gate); the service
    records the consent (scope/method/version) atomically with the connection.
  - extra='forbid' on requests; `password` is write-only and bounded.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.connectors.enums import ConnectorType


class AllowedConnectorTypeResponse(BaseModel):
    """Whether the calling user may self-connect a connector type (drives the panel cards)."""

    model_config = ConfigDict(from_attributes=True)

    connector_type: str
    allowed: bool
    # Friendly denial reason when not allowed (e.g. "not in your company's plan"); None if allowed.
    reason: str | None = None


class ConsentInput(BaseModel):
    """The Art. 7 consent a user gives at self-connect (HITL). accepted must be true."""

    model_config = ConfigDict(extra="forbid")

    accepted: bool
    scope: str = Field(default="mailbox:read", min_length=1, max_length=120)
    consent_version: str = Field(default="v1", min_length=1, max_length=40)


class SelfConnectRequest(BaseModel):
    """Connect MY OWN mailbox (CO-01 Tier 3): IMAP params + write-only password + consent."""

    model_config = ConfigDict(extra="forbid")

    connector_type: ConnectorType = ConnectorType.imap
    display_name: str = Field(min_length=1, max_length=120)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=993, ge=1, le=65535)
    use_ssl: bool = True
    username: str = Field(min_length=1, max_length=320)
    # Write-only: stored encrypted, never returned. Bounded to a sane credential length.
    password: str = Field(min_length=1, max_length=1024)
    consent: ConsentInput
