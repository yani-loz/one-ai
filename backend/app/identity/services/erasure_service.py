"""
Role: GDPR erasure (offboarding) + the exportable compliance artifact. Erases a tenant's
      personal data — UNLESS the org is under legal hold — and produces an honest deletion
      certificate; serves a compliance export bundling org metadata + the audit trail. Holds
      the erasure business logic (rule A5).
Used by: routes.erasure_routes; constructed in identity.dependencies on a PLAIN session
         (platform spans all orgs) with the hooks from app.common.erasure_hooks.
Depends on: organization/user/refresh-token/support-grant repositories, AuditService,
            app.common.erasure_hooks (the feature-module seam), identity.principal/enums/
            exceptions/schemas, security.password (async sudo re-auth).
Key invariants:
  - LEGAL-HOLD-BEATS-ERASURE: if the org is under legal hold, erase raises LegalHoldError
    (409) and touches NOTHING. Guards run before any delete: slug confirmation (400) → a
    sudo-style password re-auth of the acting admin (403; a DEACTIVATED admin gets the same
    generic failure, and the bcrypt check runs async + against a dummy hash when the account
    is missing/inactive, so no timing oracle) → legal hold (409).
  - ATOMIC: all deletes/scrubs + every erasure hook + the org.erased audit row commit in ONE
    request transaction (get_session) — a partial erasure can't be left behind.
  - COMPLETE across tenant PII: deletes refresh tokens (they key on the users' ids), SCRUBS
    support_grant.decided_by_email (a tenant subject), runs EVERY registered erasure hook
    (Connect tables + the entity graph — CA-CONN-01/03) on the same session, THEN deletes the
    users — hooks-before-users so a CO-01 user-owned connector_connection's ON DELETE CASCADE
    can't erase the connector corpus before the hook counts it (the certificate would otherwise
    underreport it as zero). The certificate reports each hook's per-table counts. The hooks run
    on the RLS-EXEMPT global session BY DESIGN, so each hook's own org-scoped SQL is the only
    containment. FAIL-CLOSED: a registry missing ANY required module (REQUIRED_ERASURE_HOOKS —
    empty AND partial configurations both) refuses to erase at all
    (ErasureNotConfiguredError -> 500) — a process that skipped create_app()'s hook
    registration must never emit a certificate that omits Connect/entity PII.
    The append-only audit_log is the one store that CANNOT be deleted (the
    immutability trigger), so its actor_email is RETAINED under a documented legal basis —
    the certificate says so. ⚠️ Any NEW tenant-scoped table MUST join an erasure hook (or be
    added here) — see the FIX_BEFORE_PROD erasure invariant.
  - The org row is retained at status='offboarded' as the subject of the compliance record.
  - Platform-only; content-blind (counts + metadata, never tenant content).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.erasure_hooks import REQUIRED_ERASURE_HOOKS, ErasureHook
from app.identity.enums import AuditActorType, OrganizationStatus
from app.identity.exceptions import (
    ErasureConfirmationError,
    ErasureNotConfiguredError,
    LegalHoldError,
    OrganizationNotFoundError,
    PasswordConfirmationError,
)
from app.identity.principal import Principal
from app.identity.repositories.organization_repository import OrganizationRepository
from app.identity.repositories.platform_admin_repository import PlatformAdminRepository
from app.identity.repositories.refresh_token_repository import RefreshTokenRepository
from app.identity.repositories.support_grant_repository import SupportGrantRepository
from app.identity.repositories.user_repository import UserRepository
from app.identity.schemas.erasure_schemas import (
    ComplianceExportResponse,
    ErasureCertificateResponse,
    ErasureRequest,
)
from app.identity.schemas.platform_schemas import OrganizationDetailResponse
from app.identity.security.password import DUMMY_PASSWORD_HASH, verify_password_async
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
        session: AsyncSession,
        organizations: OrganizationRepository,
        users: UserRepository,
        refresh_tokens: RefreshTokenRepository,
        support_grants: SupportGrantRepository,
        platform_admins: PlatformAdminRepository,
        audit: AuditService,
        erasure_hooks: Mapping[str, ErasureHook],
    ) -> None:
        """Wire the repositories + audit writer (all bound to one plain platform session).

        `session` is that same plain (RLS-exempt) session — passed to each erasure hook so
        the feature-module deletes commit atomically with the identity-side erasure.
        `erasure_hooks` is REQUIRED (dependencies injects app.common.erasure_hooks'
        registry): forcing the caller to supply it is what keeps a forgotten wiring from
        silently reintroducing the partial-erasure bug.
        """
        self._session = session
        self._organizations = organizations
        self._users = users
        self._refresh_tokens = refresh_tokens
        self._support_grants = support_grants
        self._platform_admins = platform_admins
        self._audit = audit
        self._erasure_hooks = erasure_hooks

    async def erase_organization(
        self, org_id: UUID, payload: ErasureRequest, actor: Principal
    ) -> ErasureCertificateResponse:
        """Erase a tenant's personal data and return the deletion certificate.

        Order: LOCK the org (FOR UPDATE) → confirm slug (400) → re-auth password (403; a
        missing OR deactivated admin fails identically, against a dummy hash so timing can't
        tell which) → legal-hold guard (409, touch nothing) → delete tokens → scrub support
        emails → run every registered erasure hook (Connect + entity graph) → delete users →
        offboard → audit. All atomic. (Hooks run BEFORE the user delete so a user-owned
        connection's ON DELETE CASCADE can't erase the connector corpus uncounted — see below.)
        The row lock closes a TOCTOU: a concurrent
        set_legal_hold can't slip a hold in between the legal_hold read and the deletes — it
        blocks until this transaction commits, so a hold placed as a purge looms is never
        overwritten by an in-flight erase.

        Raises:
            OrganizationNotFoundError: no such org (-> 404).
            ErasureConfirmationError: confirm_slug != the org's slug (-> 400, nothing deleted).
            PasswordConfirmationError: the admin's password re-check failed, or the admin is
                missing/deactivated (-> 403, nothing deleted, same generic message) — a
                sudo-style guard, even though the admin is already authenticated.
            LegalHoldError: the org is under legal hold (-> 409, nothing deleted).
            ErasureNotConfiguredError: any REQUIRED erasure hook is unregistered (-> 500,
                nothing deleted) — fail-closed on empty AND partial registries: a missing
                module means its PII would survive behind a clean certificate (a wiring
                error, e.g. a process that never ran create_app()).
        """
        # Fail-closed on PARTIAL configuration too, not just empty: a process registering only
        # one module would erase incompletely behind a clean certificate (cross-vendor review).
        missing_hooks = [name for name in REQUIRED_ERASURE_HOOKS if name not in self._erasure_hooks]
        if missing_hooks:
            raise ErasureNotConfiguredError(
                "Erasure hooks missing for required module(s) "
                f"{', '.join(missing_hooks)} — refusing to erase: the certificate would "
                "silently omit that module's PII. Run app.main.create_app() (it registers "
                "every required hook) before erasing."
            )
        organization = await self._organizations.get_for_update(org_id)
        if organization is None:
            raise OrganizationNotFoundError("Organization not found.")
        if payload.confirm_slug != organization.slug:
            raise ErasureConfirmationError("Confirmation does not match the organization's slug.")
        admin = await self._platform_admins.get_by_id(actor.subject_id)
        # Always pay the bcrypt cost (dummy hash when the admin is missing/deactivated) so the
        # generic 403 can't be told apart by response time; async so it never blocks the loop.
        admin_eligible = admin is not None and admin.is_active
        password_hash = admin.password_hash if admin_eligible else DUMMY_PASSWORD_HASH
        password_ok = await verify_password_async(payload.password, password_hash)
        if not admin_eligible or not password_ok:
            raise PasswordConfirmationError("Password confirmation failed.")
        if organization.legal_hold:
            raise LegalHoldError(
                "Cannot erase an organization under legal hold. Clear the hold first."
            )

        tokens_deleted = await self._refresh_tokens.delete_for_org_users(org_id)
        emails_scrubbed = await self._support_grants.scrub_decider_emails(org_id)
        # Feature-module erasure (Connect tables + the entity graph — CA-CONN-01/03): every
        # registered hook runs in THIS transaction on the shared session; each hook's SQL is
        # org-scoped (the only containment on the RLS-exempt session). Counts merge into one
        # per-table report (hooks own disjoint tables).
        #
        # RUN BEFORE deleting users (cross-vendor review P2): a CO-01 user-owned
        # connector_connection FKs users(org_id, id) ON DELETE CASCADE, so deleting users FIRST
        # would cascade-remove the connection + its email/sync children before the Connect hook
        # could count them — the certificate would then UNDERREPORT the connector corpus as zero
        # for the default self-connect path. Erasing the feature tables explicitly first counts
        # them honestly; the user delete below then has nothing connector-related left to cascade.
        erased_rows_by_table: dict[str, int] = {}
        for hook in self._erasure_hooks.values():
            erased_rows_by_table.update(await hook(org_id, self._session))
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
                    "erased_rows_by_table": erased_rows_by_table,
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
            erased_rows_by_table=erased_rows_by_table,
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
