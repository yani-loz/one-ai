"""
Role: Pydantic request/response models for company user management (/users/*).
Used by: routes.user_routes (validation + serialization), UserService.
Depends on: app.identity.enums (UserRole). pydantic + email-validator (external).
Key invariants:
  - Input validation here (EmailStr, role enum, length bounds) before the service runs.
  - Email is canonicalized to lowercase (NormalizedEmail) so case variants map to one
    identity (DYN-02); free-text names reject control chars (SafeName, DYN-03); request
    models forbid unknown fields (extra='forbid', DYN-04).
  - UserResponse NEVER includes password_hash. Roles are constrained to UserRole, so
    a request can never create a platform admin through this surface.
  - org_id is set by the service from the caller's verified token, never from the body.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from app.identity.enums import UserRole

_BCRYPT_MAX_PASSWORD_BYTES = 72  # bcrypt 5.x RAISES (does not truncate) above this.


def _within_bcrypt_byte_limit(value: str) -> str:
    """Reject a password whose UTF-8 encoding exceeds bcrypt's 72-byte input limit.

    bcrypt 5.x raises ValueError on >72-byte inputs instead of truncating, so hashing
    an over-long password would surface as an opaque 500; enforcing the real (byte)
    limit at the validation boundary turns it into a clean 422. The field's max_length
    bounds CHARACTERS; this bounds BYTES (a multibyte password can exceed 72 bytes well
    under 72 characters).
    """
    if len(value.encode("utf-8")) > _BCRYPT_MAX_PASSWORD_BYTES:
        raise ValueError(f"password must be at most {_BCRYPT_MAX_PASSWORD_BYTES} bytes")
    return value


# Reusable password type: 8..128 chars AND <= 72 UTF-8 bytes (bcrypt's hard limit).
BcryptPassword = Annotated[
    str, Field(min_length=8, max_length=128), AfterValidator(_within_bcrypt_byte_limit)
]

_DEL = 0x7F


def _normalize_email(value: str) -> str:
    """Lowercase the whole email so case variants map to one identity (DYN-02).

    EmailStr already lowercases the domain; this also lowercases the local-part, so
    'Foo.Bar@x' and 'foo.bar@x' canonicalize to a single value on both store and login
    (case-insensitive login + a real global-uniqueness guarantee).
    """
    return value.lower()


def _reject_control_chars(value: str) -> str:
    """Reject NUL / C0 control characters in a free-text field (DYN-03).

    A NUL byte passes Pydantic's length check but makes asyncpg raise on INSERT
    (CharacterNotInRepertoireError) -> an opaque 500. Rejecting control chars at the
    validation boundary returns a clean 422 instead.
    """
    if any(ord(ch) < 0x20 or ord(ch) == _DEL for ch in value):
        raise ValueError("must not contain control characters")
    return value


# Email canonicalized to lowercase (EmailStr validates format first).
NormalizedEmail = Annotated[EmailStr, AfterValidator(_normalize_email)]

# Bounded free-text name with no control characters (1..200 chars).
SafeName = Annotated[
    str, Field(min_length=1, max_length=200), AfterValidator(_reject_control_chars)
]


class UserCreateRequest(BaseModel):
    """Payload to create a new user in the caller's org (company_admin only)."""

    model_config = ConfigDict(extra="forbid")

    email: NormalizedEmail
    full_name: SafeName
    role: UserRole
    password: BcryptPassword


class UserUpdateRequest(BaseModel):
    """Partial update for an existing user. All fields optional."""

    model_config = ConfigDict(extra="forbid")

    full_name: SafeName | None = None
    role: UserRole | None = None
    is_active: bool | None = None


class UserResponse(BaseModel):
    """A user as returned by the management + onboarding endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool
    org_id: UUID
    created_at: datetime
