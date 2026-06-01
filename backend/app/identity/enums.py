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


class OrganizationStatus(StrEnum):
    """Lifecycle status of a customer organization (platform-managed).

    Values match the DB CHECK on organizations.status (ck_organizations_status).
    `suspended` blocks the org's company logins (and refresh) without deleting data.
    """

    active = "active"
    suspended = "suspended"
    onboarding = "onboarding"
    offboarded = "offboarded"


class SupportGrantStatus(StrEnum):
    """Lifecycle state of a break-glass support-access grant.

    Values match the DB CHECK on support_grant.status (ck_support_grant_status). There is
    deliberately NO `expired` value — expiry is computed live against `expires_at`, never
    stored, so the access decision always reads the clock rather than a column.
    """

    requested = "requested"
    approved = "approved"
    denied = "denied"
    revoked = "revoked"


class AuditActorType(StrEnum):
    """Who performed an audited action (pins audit_log.actor_type via ck_audit_log_actor_type).

    `user` and `platform_admin` mirror SubjectType; `system` covers actions with no human
    actor. Room is reserved for `agent` once Connect/Ask/Learn emit AI actions — that value
    must be added to the DB CHECK before it is used.
    """

    platform_admin = "platform_admin"
    user = "user"
    system = "system"
