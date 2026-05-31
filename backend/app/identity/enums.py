"""
Role: Enumerations for the identity domain — user roles and token subject types.
Used by: identity models, schemas, services, dependencies, security.tokens.
Depends on: nothing internal — leaf module.
Key invariants:
  - UserRole values match the DB CHECK on users.role ('company_admin','member').
  - SubjectType values match the DB CHECK on refresh_tokens.subject_type and the
    JWT audience domains (a 'user' carries aud='company'; 'platform_admin' carries
    aud='platform').
"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    """Role of an org-scoped user (company login domain)."""

    company_admin = "company_admin"
    member = "member"


class SubjectType(StrEnum):
    """Type of authenticated subject — distinguishes the two auth domains."""

    user = "user"
    platform_admin = "platform_admin"
