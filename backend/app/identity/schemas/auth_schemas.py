"""
Role: Pydantic request/response models for the company auth endpoints (/auth/*).
Used by: routes.auth_routes (validation + serialization), services build the
         responses.
Depends on: app.identity.enums (UserRole). pydantic + email-validator (external).
Key invariants:
  - Input validation happens here (EmailStr, length bounds) before any service runs.
  - Responses NEVER include a password, hash, or token-hash. Tokens returned are the
    raw issued strings, by design.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.identity.enums import UserRole
from app.identity.schemas.user_schemas import NormalizedEmail


class LoginRequest(BaseModel):
    """Credentials for company-user login."""

    model_config = ConfigDict(extra="forbid")

    email: NormalizedEmail
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    """A presented opaque refresh token to rotate."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=1, max_length=512)


class LogoutRequest(BaseModel):
    """A presented opaque refresh token to revoke."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=1, max_length=512)


class AuthenticatedUserResponse(BaseModel):
    """The authenticated company user (returned by /auth/login and /auth/me)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str
    role: UserRole
    org_id: UUID
    org_name: str


class TokenPairResponse(BaseModel):
    """An issued access + refresh token pair.

    `user` is populated on login (company domain) and omitted on refresh and on the
    platform domain.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: AuthenticatedUserResponse | None = None
