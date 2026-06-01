"""
Role: GDPR erasure (offboarding) + the exportable compliance artifact. Erases a tenant's
      personal data — UNLESS the org is under legal hold — and produces an honest deletion
      certificate; serves a compliance export bundling org metadata + the audit trail. Holds
      the erasure business logic (rule A5).
Used by: routes.erasure_routes; constructed in identity.dependencies on a PLAIN session
         (platform spans all orgs).
Depends on: organization/user/refresh-token/support-grant repositories, AuditService,
            identity.principal/enums/exceptions/schemas.
Key invariants:
  - LEGAL-HOLD-BEATS-ERASURE: if the org is under legal hold, erase raises LegalHoldError
    (409) and touches NOTHING. The slug confirmation is checked first (400 on mismatch).
  - ATOMIC: all deletes/scrubs + the org.erased audit row commit in ONE request transaction
    (get_session) — a partial erasure can't be left behind.
  - COMPLETE across tenant PII: deletes users + refresh tokens (tokens FIRST — they key on
    the users' ids), and SCRUBS support_grant.decided_by_email (a tenant subject). The
    append-only audit_log is the one store that CANNOT be deleted (the immutability trigger),
    so its actor_email is RETAINED under a documented legal basis — the certificate says so.
    ⚠️ Any NEW tenant-scoped table MUST be added here (see FIX_BEFORE_PROD erasure invariant).
  - The org row is retained at status='offboarded' as the subject of the compliance record.
  - Platform-only; content-blind (counts + metadata, never tenant content).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.identity.enums import AuditActorType, OrganizationStatus
from app.identity.exceptions import (
    ErasureConfirmationError,
    LegalHoldError,
    OrganizationNotFoundError,
)
from app.identity.principal import Principal
from app.identity.repositories.organization_repository import OrganizationRepository
from app.identity.repositories.refresh_token_repository import RefreshTokenRepository
from app.identity.repositories.support_grant_repository import SupportGrantRepository
from app.identity.repositories.user_repository import UserRepository
from app.identity.schemas.erasure_schemas import (
    ComplianceExportResponse,
    ErasureCertificateResponse,
    ErasureRequest,
)
from app.identity.schemas.platform_schemas import OrganizationDetailResponse
from app.identity.services.audit_service import AuditAction, AuditEvent, AuditService

_ENTITY_ORGANIZATION = "organization"
_LEGAL_BASIS = (
    "GDPR Art. 17(3) — the append-only audit_log is retained as a legal/compliance "
    "obligation and for the defence of legal claims; actor_email pseudonymization is tracked."
)
# Compliance export caps the trail; a streaming/paged export is the production version.
_MAX_EXPORT_ENTRIES = 1000


class ErasureService:
    """Tenant erasure (legal-hold-gated) + the compliance export."""

    def __init__(
        self,
        organizations: OrganizationRepository,
        users: UserRepository,
        refresh_tokens: RefreshTokenRepository,
        support_grants: SupportGrantRepository,
        audit: AuditService,
    ) -> None:
        """Wire the repositories + audit writer (all bound to one plain platform session)."""
        self._organizations = organizations
        self._users = users
        self._refresh_tokens = refresh_tokens
        self._support_grants = support_grants
        self._audit = audit

    async def erase_organization(
        self, org_id: UUID, payload: ErasureRequest, actor: Principal
    ) -> ErasureCertificateResponse:
        """Erase a tenant's personal data and return the deletion certificate.

        Order: confirm slug (400) → legal-hold guard (409, touch nothing) → delete tokens →
        scrub support emails → delete users → offboard → audit. All atomic.

        Raises:
            OrganizationNotFoundError: no such org (-> 404).
            ErasureConfirmationError: confirm_slug != the org's slug (-> 400, nothing deleted).
            LegalHoldError: the org is under legal hold (-> 409, nothing deleted).
        """
        organization = await self._organizations.get_by_id(org_id)
        if organization is None:
            raise OrganizationNotFoundError("Organization not found.")
        if payload.confirm_slug != organization.slug:
            raise ErasureConfirmationError(
                "Confirmation does not match the organization's slug."
            )
        if organization.legal_hold:
            raise LegalHoldError(
                "Cannot erase an organization under legal hold. Clear the hold first."
            )

        tokens_deleted = await self._refresh_tokens.delete_for_org_users(org_id)
        emails_scrubbed = await self._support_grants.scrub_decider_emails(org_id)
        users_erased = await self._users.delete_all_in_org(org_id)
        organization.status = OrganizationStatus.offboarded.value
        erased_at = datetime.now(UTC)

        await self._audit.record(
            AuditEvent(
                action=AuditAction.ORG_ERASED,
                actor_type=AuditActorType.platform_admin,
                actor_id=actor.subject_id,
                org_id=org_id,
                entity_type=_ENTITY_ORGANIZATION,
                entity_id=org_id,
                details={
                    "users_erased": users_erased,
                    "tokens_deleted": tokens_deleted,
                    "support_decider_emails_scrubbed": emails_scrubbed,
                    "audit_log_retained": True,
                    "reason": payload.reason,
                },
            )
        )
        return ErasureCertificateResponse(
            org_id=org_id,
            org_slug=organization.slug,
            org_name=organization.name,
            status=organization.status,
            erased_at=erased_at,
            erased_by_admin_id=actor.subject_id,
            users_erased=users_erased,
            tokens_deleted=tokens_deleted,
            support_decider_emails_scrubbed=emails_scrubbed,
            audit_log_retained=True,
            retained_legal_basis=_LEGAL_BASIS,
        )

    async def export_compliance(self, org_id: UUID) -> ComplianceExportResponse:
        """Build the compliance export for one org — metadata + its full audit trail.

        Raises:
            OrganizationNotFoundError: no such org (-> 404).
        """
        row = await self._organizations.get_with_user_count(org_id)
        if row is None:
            raise OrganizationNotFoundError("Organization not found.")
        organization, user_count = row
        detail = OrganizationDetailResponse(
            id=organization.id,
            name=organization.name,
            slug=organization.slug,
            status=organization.status,
            user_count=user_count,
            legal_hold=organization.legal_hold,
            created_at=organization.created_at,
        )
        audit = await self._audit.list_for_org(org_id, limit=_MAX_EXPORT_ENTRIES, offset=0)
        return ComplianceExportResponse(
            organization=detail, audit=audit, generated_at=datetime.now(UTC)
        )
