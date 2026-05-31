"""
Role: Principal — the verified identity of the caller, decoded from a valid JWT.
Used by: identity.dependencies (builds it from access-token claims), services and
         routes (read role / org_id), security.tokens (encodes one into a token).
Depends on: nothing internal — leaf module.
Key invariants:
  - A Principal exists ONLY after JWT signature + audience + expiry have been
    verified. Nothing constructs one from unverified input.
  - org_id is None for platform admins (global scope, not an org row) and a UUID
    for org-scoped users. subject_type distinguishes the two.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Principal:
    """Immutable, verified caller identity.

    Attributes:
        subject_id: the user id or platform-admin id (JWT `sub`).
        org_id: the user's organization, or None for platform admins.
        role: 'company_admin' | 'member' for users; 'platform_admin' for admins.
        subject_type: 'user' | 'platform_admin' (matches SubjectType / JWT audience).
    """

    subject_id: UUID
    org_id: UUID | None
    role: str
    subject_type: str
