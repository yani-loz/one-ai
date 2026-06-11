"""
Role: Pydantic models for GDPR erasure + compliance export (/platform/orgs/{id}/erase,
      .../compliance-export).
Used by: routes.erasure_routes, services.erasure_service.
Depends on: pydantic; platform_schemas (OrganizationDetailResponse), audit_schemas
            (AuditLogEntryResponse).
Key invariants:
  - The deletion certificate is HONEST about retain-vs-erase: it reports what was deleted
    (users, tokens, and every feature-module table via `erased_rows_by_table` — Connect +
    the entity graph), what was pseudonymized (support-grant decider emails), AND what was
    lawfully RETAINED (the append-only audit_log) under a documented legal basis — it never
    claims total erasure.
  - `erased_rows_by_table` is ADDITIVE and defaults to {} — the certificate shape stays
    backward-compatible for existing consumers (the FE erasure panel ignores it).
  - Metadata only — the certificate + export describe ACTIONS and counts, never tenant content.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.identity.schemas.audit_schemas import AuditLogEntryResponse
from app.identity.schemas.platform_schemas import OrganizationDetailResponse
from app.identity.schemas.user_schemas import LoginPassword


class ErasureRequest(BaseModel):
    """A platform admin's request to erase a tenant's personal data (offboarding)."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)
    # Must echo the org's slug exactly — a GitHub-style guard against accidental destruction.
    confirm_slug: str = Field(min_length=1, max_length=100)
    # Sudo-style re-authentication: the admin re-enters their own password (bounded like login
    # so an over-length value can't be run through bcrypt). Verified before any delete.
    password: LoginPassword


class ErasureCertificateResponse(BaseModel):
    """Proof of erasure: what was deleted, what was lawfully retained, by whom, when."""

    org_id: UUID
    org_slug: str
    org_name: str
    status: str
    erased_at: datetime
    erased_by_admin_id: UUID
    # Erased / pseudonymized:
    users_erased: int
    tokens_deleted: int
    support_decider_emails_scrubbed: int
    # Per-table rows deleted by the registered feature-module erasure hooks (Connect tables +
    # the entity graph). Additive optional field — defaults to {} for backward compatibility.
    erased_rows_by_table: dict[str, int] = Field(default_factory=dict)
    # Lawfully retained (append-only audit trail can't be deleted — see legal_basis):
    audit_log_retained: bool
    retained_legal_basis: str


class ComplianceExportResponse(BaseModel):
    """An exportable compliance artifact for one org: metadata + its full audit trail."""

    organization: OrganizationDetailResponse
    audit: list[AuditLogEntryResponse]
    generated_at: datetime
