"""
Role: Pydantic request/response models for the platform domain (/platform/*).
Used by: routes.platform_routes (validation + serialization), PlatformAuthService.
Depends on: app.identity.schemas.user_schemas (UserResponse for the created admin).
            pydantic + email-validator (external).
Key invariants:
  - OrganizationResponse exposes METADATA ONLY (id/name/slug/status/user_count/
    created_at) — never tenant content, costs, or token usage (SPEC §4).
  - Onboarding creates the org and its first company_admin atomically; the request
    carries that admin's seed credentials, validated here before the service runs.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.identity.schemas.user_schemas import (
    BcryptPassword,
    NormalizedEmail,
    SafeName,
    UserResponse,
)


class PlatformLoginRequest(BaseModel):
    """Credentials for platform-admin login (separate auth domain)."""

    model_config = ConfigDict(extra="forbid")

    email: NormalizedEmail
    password: str = Field(min_length=1, max_length=256)


class OrganizationCreateRequest(BaseModel):
    """Payload to onboard a new organization plus its first company_admin."""

    model_config = ConfigDict(extra="forbid")

    org_name: SafeName
    org_slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9-]*$")
    admin_email: NormalizedEmail
    admin_full_name: SafeName
    admin_password: BcryptPassword


class OrganizationResponse(BaseModel):
    """An organization as metadata (no tenant content)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    status: str
    user_count: int
    created_at: datetime


class OrganizationOnboardedResponse(BaseModel):
    """Result of onboarding: the new org and its first company_admin."""

    organization: OrganizationResponse
    admin: UserResponse
