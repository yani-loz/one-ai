"""
Service-level tests for ErasureService — real DB + real repositories (no internal mocks).

Covers the hardened sudo re-auth (a DEACTIVATED admin gets the same generic
PasswordConfirmationError, and nothing is deleted) and the erasure-hook seam: registered
hooks run inside the erasure with (org_id, session), their per-table counts surface on the
certificate, and NO hook runs when a guard (legal hold / failed re-auth) fires. The hooks
here are FAKES on purpose — the registry is the module boundary (the real connector/entity
hooks get their own repo-layer tests). Requires Postgres (identity_schema fixture).
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common import erasure_hooks as erasure_hook_registry
from app.common.erasure_hooks import ErasureHook
from app.identity.enums import UserRole
from app.identity.exceptions import (
    ErasureNotConfiguredError,
    LegalHoldError,
    PasswordConfirmationError,
)
from app.identity.principal import Principal
from app.identity.repositories.audit_repository import AuditRepository
from app.identity.repositories.organization_repository import OrganizationRepository
from app.identity.repositories.platform_admin_repository import PlatformAdminRepository
from app.identity.repositories.refresh_token_repository import RefreshTokenRepository
from app.identity.repositories.support_grant_repository import SupportGrantRepository
from app.identity.repositories.user_repository import UserRepository
from app.identity.schemas.erasure_schemas import ErasureRequest
from app.identity.services.audit_service import AuditService
from app.identity.services.erasure_service import ErasureService
from tests.identity.conftest import seed_organization, seed_platform_admin, seed_user

_ADMIN_PASSWORD = "Test-Pass-123"  # seed_platform_admin default — re-entered for sudo re-auth
_REASON = "Customer offboarding per contract termination."


class _RecordingHook:
    """A fake erasure hook that records its (org_id, session) calls and returns set counts."""

    def __init__(self, counts: dict[str, int]) -> None:
        self.counts = counts
        self.calls: list[tuple[UUID, AsyncSession]] = []

    async def __call__(self, org_id: UUID, session: AsyncSession) -> dict[str, int]:
        self.calls.append((org_id, session))
        return dict(self.counts)


def _service(session: AsyncSession, hooks: dict[str, ErasureHook]) -> ErasureService:
    """Construct ErasureService on real repositories bound to `session` (mirrors dependencies)."""
    return ErasureService(
        session=session,
        organizations=OrganizationRepository(session),
        users=UserRepository(session),
        refresh_tokens=RefreshTokenRepository(session),
        support_grants=SupportGrantRepository(session),
        platform_admins=PlatformAdminRepository(session),
        audit=AuditService(AuditRepository(session)),
        erasure_hooks=hooks,
    )


async def _seed_org_admin_user(
    session: AsyncSession,
    *,
    slug: str = "acme",
    admin_active: bool = True,
    legal_hold: bool = False,
):
    """Seed one org (+ a company user) and a platform admin; return (org, admin, actor)."""
    org = await seed_organization(session, name=slug.title(), slug=slug)
    org.legal_hold = legal_hold
    await seed_user(
        session,
        org_id=org.id,
        email=f"admin@{slug}.example",
        full_name="Company Admin",
        role=UserRole.company_admin,
    )
    admin = await seed_platform_admin(
        session, email=f"staff-{slug}@ethera.example", full_name="Staff", is_active=admin_active
    )
    actor = Principal(
        subject_id=admin.id, org_id=None, role="platform_admin", subject_type="platform_admin"
    )
    return org, admin, actor


async def _org_user_count(session: AsyncSession, org_id: UUID) -> int:
    result = await session.execute(
        text("SELECT count(*) FROM users WHERE org_id=:o"), {"o": str(org_id)}
    )
    return result.scalar_one()


async def test_erase_deactivated_admin_sudo_reauth_rejected(db_session: AsyncSession) -> None:
    # A deactivated admin with the CORRECT password gets the same generic failure — and
    # neither the identity deletes nor any erasure hook runs.
    org, _admin, actor = await _seed_org_admin_user(db_session, admin_active=False)
    hook = _RecordingHook({"person": 1})
    service = _service(db_session, {"connectors": hook, "entities": hook})
    payload = ErasureRequest(reason=_REASON, confirm_slug="acme", password=_ADMIN_PASSWORD)

    with pytest.raises(PasswordConfirmationError):
        await service.erase_organization(org.id, payload, actor)

    assert hook.calls == []
    assert await _org_user_count(db_session, org.id) == 1


async def test_erase_runs_registered_hooks_and_reports_per_table_counts(
    db_session: AsyncSession,
) -> None:
    # Every registered hook runs with (org_id, the service's session) and the certificate
    # reports the merged per-table counts — the certificate no longer lies about Connect PII.
    org, _admin, actor = await _seed_org_admin_user(db_session)
    connect_hook = _RecordingHook({"connector_connection": 2, "email_message": 5})
    entity_hook = _RecordingHook({"person": 3})
    service = _service(db_session, {"connectors": connect_hook, "entities": entity_hook})
    payload = ErasureRequest(reason=_REASON, confirm_slug="acme", password=_ADMIN_PASSWORD)

    certificate = await service.erase_organization(org.id, payload, actor)

    assert certificate.erased_rows_by_table == {
        "connector_connection": 2,
        "email_message": 5,
        "person": 3,
    }
    assert connect_hook.calls == [(org.id, db_session)]
    assert entity_hook.calls == [(org.id, db_session)]
    assert certificate.status == "offboarded"


async def test_erase_legal_hold_runs_no_hooks(db_session: AsyncSession) -> None:
    # LEGAL-HOLD-BEATS-ERASURE extends to the hooks: the 409 guard fires before any hook runs.
    org, _admin, actor = await _seed_org_admin_user(db_session, legal_hold=True)
    hook = _RecordingHook({"connector_connection": 1})
    service = _service(db_session, {"connectors": hook, "entities": hook})
    payload = ErasureRequest(reason=_REASON, confirm_slug="acme", password=_ADMIN_PASSWORD)

    with pytest.raises(LegalHoldError):
        await service.erase_organization(org.id, payload, actor)

    assert hook.calls == []
    assert await _org_user_count(db_session, org.id) == 1


async def test_erase_empty_hook_registry_fails_closed_and_deletes_nothing(
    db_session: AsyncSession,
) -> None:
    # FAIL-CLOSED: a process that never ran create_app() has an empty registry; erasing there
    # would emit a certificate while Connect/entity PII silently survives. The service must
    # refuse loudly BEFORE any delete — not erase partially.
    org, _admin, actor = await _seed_org_admin_user(db_session)
    service = _service(db_session, {})
    payload = ErasureRequest(reason=_REASON, confirm_slug="acme", password=_ADMIN_PASSWORD)

    with pytest.raises(ErasureNotConfiguredError):
        await service.erase_organization(org.id, payload, actor)

    assert await _org_user_count(db_session, org.id) == 1


def test_register_erasure_hook_same_name_replaces_not_stacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Registry semantics the service contract relies on: insertion order is preserved and
    # re-registering a name REPLACES the hook (idempotent create_app), never stacks it.
    monkeypatch.setattr(erasure_hook_registry, "_hooks", {})

    async def first_hook(org_id: UUID, session: AsyncSession) -> dict[str, int]:
        return {}

    async def second_hook(org_id: UUID, session: AsyncSession) -> dict[str, int]:
        return {}

    erasure_hook_registry.register_erasure_hook("connectors", first_hook)
    erasure_hook_registry.register_erasure_hook("entities", second_hook)
    erasure_hook_registry.register_erasure_hook("connectors", second_hook)

    hooks = erasure_hook_registry.registered_erasure_hooks()
    assert list(hooks) == ["connectors", "entities"]
    assert hooks["connectors"] is second_hook


@pytest.mark.parametrize("registered", ["connectors", "entities"])
async def test_erase_partial_hook_registry_fails_closed(
    db_session: AsyncSession, registered: str
) -> None:
    # 2026-06-11 cross-vendor (GPT) review: an empty-registry guard alone fails OPEN on partial
    # configuration — only one module registered would erase incompletely behind a clean
    # certificate. Every name in REQUIRED_ERASURE_HOOKS must be present, or nothing is deleted.
    org, _admin, actor = await _seed_org_admin_user(db_session)
    hook = _RecordingHook({"some_table": 1})
    service = _service(db_session, {registered: hook})
    payload = ErasureRequest(reason=_REASON, confirm_slug="acme", password=_ADMIN_PASSWORD)

    with pytest.raises(ErasureNotConfiguredError):
        await service.erase_organization(org.id, payload, actor)

    assert hook.calls == []
    assert await _org_user_count(db_session, org.id) == 1


async def test_erase_hook_failure_rolls_back_identity_deletes(db_session: AsyncSession) -> None:
    # Atomicity proof for the ATOMIC invariant: when a LATER hook raises, the identity-side
    # deletes that already ran in the same transaction must roll back — a partial erasure can
    # never persist behind the raised error.
    org, _admin, actor = await _seed_org_admin_user(db_session)
    org_id = org.id  # plain UUID — the ORM instance expires across the commit/rollback below
    # Make the seed DURABLE before acting: the erase + rollback must revert ONLY the erasure's
    # own work, and an uncommitted seed would roll back with it (false negative).
    await db_session.commit()

    class _ExplodingHook:
        async def __call__(self, org_id: UUID, session: AsyncSession) -> dict[str, int]:
            raise RuntimeError("hook blew up mid-erasure")

    service = _service(
        db_session,
        {"connectors": _RecordingHook({"connector_connection": 0}), "entities": _ExplodingHook()},
    )
    payload = ErasureRequest(reason=_REASON, confirm_slug="acme", password=_ADMIN_PASSWORD)

    with pytest.raises(RuntimeError):
        await service.erase_organization(org_id, payload, actor)
    await db_session.rollback()

    assert await _org_user_count(db_session, org_id) == 1  # users survived the failed erasure
    refreshed = await db_session.execute(
        text("SELECT status FROM organizations WHERE id=:o"), {"o": str(org_id)}
    )
    assert refreshed.scalar_one() == "active"  # org was not marked offboarded
