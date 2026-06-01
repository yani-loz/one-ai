"""
DB-backed tests for app.identity.services.user_service.

Covers org-scoped CRUD plus the NON-NEGOTIABLE service-layer cross-tenant isolation
tests (testing.md): updating/deactivating another org's user must raise
UserNotFoundError (-> 404), never silently touch the row or leak its existence.
Uses real repositories on a real session — no internal mocking. Requires Postgres.
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import scoped_session
from app.identity.enums import UserRole
from app.identity.exceptions import DuplicateUserError, LastAdminError, UserNotFoundError
from app.identity.models.audit_log import AuditLog
from app.identity.principal import Principal
from app.identity.repositories.audit_repository import AuditRepository
from app.identity.repositories.user_repository import UserRepository
from app.identity.schemas.user_schemas import (
    UserCreateRequest,
    UserUpdateRequest,
)
from app.identity.services.audit_service import AuditService
from app.identity.services.user_service import UserService
from tests.identity.conftest import seed_organization, seed_user

# The company_admin performing the action (only subject_id is recorded on the audit row).
_ACTOR = Principal(
    subject_id=uuid4(), org_id=None, role="company_admin", subject_type="user"
)


def _service(session: AsyncSession) -> UserService:
    return UserService(
        users=UserRepository(session), audit=AuditService(AuditRepository(session))
    )


async def test_create_user_persists_in_caller_org(db_session: AsyncSession) -> None:
    org = await seed_organization(db_session, name="Acme", slug="acme")
    service = _service(db_session)
    payload = UserCreateRequest(
        email="new@acme.example", full_name="New Hire", role=UserRole.member, password="StrongPass1"
    )

    created = await service.create_user(org.id, payload, _ACTOR)

    assert created.org_id == org.id
    assert created.email == "new@acme.example"
    assert created.is_active is True


async def test_create_user_duplicate_email_raises_duplicate(db_session: AsyncSession) -> None:
    org = await seed_organization(db_session, name="Acme", slug="acme")
    await seed_user(
        db_session, org_id=org.id, email="dupe@acme.example",
        full_name="Existing", role=UserRole.member,
    )
    service = _service(db_session)
    payload = UserCreateRequest(
        email="dupe@acme.example", full_name="Clash", role=UserRole.member, password="StrongPass1"
    )

    with pytest.raises(DuplicateUserError):
        await service.create_user(org.id, payload, _ACTOR)


async def test_list_users_returns_only_caller_org(db_session: AsyncSession) -> None:
    org_a = await seed_organization(db_session, name="A", slug="a")
    org_b = await seed_organization(db_session, name="B", slug="b")
    await seed_user(
        db_session, org_id=org_a.id, email="a@a.example", full_name="A", role=UserRole.member
    )
    await seed_user(
        db_session, org_id=org_b.id, email="b@b.example", full_name="B", role=UserRole.member
    )
    service = _service(db_session)

    listed = await service.list_users(org_a.id)

    assert {user.email for user in listed} == {"a@a.example"}


async def test_update_user_same_org_applies_changes(db_session: AsyncSession) -> None:
    org = await seed_organization(db_session, name="Acme", slug="acme")
    user = await seed_user(
        db_session, org_id=org.id, email="u@acme.example",
        full_name="Old Name", role=UserRole.member,
    )
    service = _service(db_session)

    updated = await service.update_user(
        org.id,
        user.id,
        UserUpdateRequest(
            full_name="New Name", role=UserRole.company_admin, is_active=False
        ),
        _ACTOR,
    )

    assert updated.full_name == "New Name"
    assert updated.role == UserRole.company_admin
    assert updated.is_active is False


async def test_update_user_cross_tenant_raises_not_found(db_session: AsyncSession) -> None:
    # THE NON-NEGOTIABLE (service layer): admin of org A patching org B's user must
    # raise UserNotFoundError (-> 404) and the B row must remain untouched.
    org_a = await seed_organization(db_session, name="A", slug="a")
    org_b = await seed_organization(db_session, name="B", slug="b")
    user_b = await seed_user(
        db_session, org_id=org_b.id, email="b@b.example", full_name="Bob B", role=UserRole.member
    )
    service = _service(db_session)

    with pytest.raises(UserNotFoundError):
        await service.update_user(
            org_a.id, user_b.id, UserUpdateRequest(full_name="HIJACKED"), _ACTOR
        )

    await db_session.refresh(user_b)
    assert user_b.full_name == "Bob B"


async def test_deactivate_user_same_org_sets_inactive(db_session: AsyncSession) -> None:
    org = await seed_organization(db_session, name="Acme", slug="acme")
    user = await seed_user(
        db_session, org_id=org.id, email="u@acme.example", full_name="U", role=UserRole.member
    )
    service = _service(db_session)

    await service.deactivate_user(org.id, user.id, _ACTOR)

    await db_session.refresh(user)
    assert user.is_active is False


async def test_deactivate_user_cross_tenant_raises_not_found(db_session: AsyncSession) -> None:
    # THE NON-NEGOTIABLE (service layer): admin of org A deactivating org B's user
    # must raise 404 and leave B's user active.
    org_a = await seed_organization(db_session, name="A", slug="a")
    org_b = await seed_organization(db_session, name="B", slug="b")
    user_b = await seed_user(
        db_session, org_id=org_b.id, email="b@b.example", full_name="Bob B", role=UserRole.member
    )
    service = _service(db_session)

    with pytest.raises(UserNotFoundError):
        await service.deactivate_user(org_a.id, user_b.id, _ACTOR)

    await db_session.refresh(user_b)
    assert user_b.is_active is True


async def _always_false(_email: str) -> bool:
    """Stub for email_exists: simulate a pre-check that misses a concurrent insert."""
    return False


async def test_create_user_integrity_error_maps_to_duplicate(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AUD-05: when the pre-check misses a concurrent insert, the users.email UNIQUE
    # violation must surface as DuplicateUserError (-> 409), not a raw 500.
    org = await seed_organization(db_session, name="Acme", slug="acme")
    await seed_user(
        db_session, org_id=org.id, email="race@acme.example",
        full_name="Existing", role=UserRole.member,
    )
    repo = UserRepository(db_session)
    monkeypatch.setattr(repo, "email_exists", _always_false)
    service = UserService(users=repo, audit=AuditService(AuditRepository(db_session)))
    payload = UserCreateRequest(
        email="race@acme.example", full_name="Racer", role=UserRole.member, password="StrongPass1"
    )

    with pytest.raises(DuplicateUserError):
        await service.create_user(org.id, payload, _ACTOR)

    await db_session.rollback()  # clear the poisoned transaction for the fixture's commit


async def test_deactivate_last_active_admin_raises_last_admin(db_session: AsyncSession) -> None:
    # AUD-07: deactivating the org's only active admin would lock the org out -> 409.
    org = await seed_organization(db_session, name="Acme", slug="acme")
    admin = await seed_user(
        db_session, org_id=org.id, email="solo@acme.example",
        full_name="Solo Admin", role=UserRole.company_admin,
    )
    service = _service(db_session)

    with pytest.raises(LastAdminError):
        await service.deactivate_user(org.id, admin.id, _ACTOR)


async def test_demote_last_active_admin_raises_last_admin(db_session: AsyncSession) -> None:
    # AUD-07: demoting the org's only active admin to member would lock the org out.
    org = await seed_organization(db_session, name="Acme", slug="acme")
    admin = await seed_user(
        db_session, org_id=org.id, email="solo@acme.example",
        full_name="Solo Admin", role=UserRole.company_admin,
    )
    service = _service(db_session)

    with pytest.raises(LastAdminError):
        await service.update_user(
            org.id, admin.id, UserUpdateRequest(role=UserRole.member), _ACTOR
        )


async def test_deactivate_admin_with_a_peer_admin_succeeds(db_session: AsyncSession) -> None:
    # The guard only blocks removing the LAST admin: with a peer admin, it must allow it.
    org = await seed_organization(db_session, name="Acme", slug="acme")
    admin_one = await seed_user(
        db_session, org_id=org.id, email="a1@acme.example",
        full_name="Admin One", role=UserRole.company_admin,
    )
    await seed_user(
        db_session, org_id=org.id, email="a2@acme.example",
        full_name="Admin Two", role=UserRole.company_admin,
    )
    service = _service(db_session)

    await service.deactivate_user(org.id, admin_one.id, _ACTOR)

    await db_session.refresh(admin_one)
    assert admin_one.is_active is False


async def _audit_rows(session: AsyncSession, action: str) -> list[AuditLog]:
    """Read back the audit rows for one action (newest-first) on the same session."""
    result = await session.execute(
        select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.occurred_at.desc())
    )
    return list(result.scalars().all())


async def test_create_user_writes_content_blind_audit_row(db_session: AsyncSession) -> None:
    # PC-04 AC3: user.create is recorded — actor + target id + role, but NEVER the target's
    # email (the audit is platform-readable; a tenant user's email is PII the platform
    # must not see). Same-session write, so it's visible on this session.
    org = await seed_organization(db_session, name="Acme", slug="acme")
    service = _service(db_session)
    payload = UserCreateRequest(
        email="audited@acme.example",
        full_name="Audited",
        role=UserRole.member,
        password="StrongPass1",
    )

    created = await service.create_user(org.id, payload, _ACTOR)

    rows = await _audit_rows(db_session, "user.create")
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_id == _ACTOR.subject_id
    assert row.actor_type == "user"
    assert row.org_id == org.id
    assert row.entity_type == "user"
    assert row.entity_id == created.id
    assert row.details == {"role": "member"}
    assert row.actor_email is None
    assert "audited@acme.example" not in json.dumps(row.details)  # content-blind


async def test_role_change_emits_audit_row(db_session: AsyncSession) -> None:
    # Promoting a member to admin records user.role_change with from/to roles (no guard trip).
    org = await seed_organization(db_session, name="Acme", slug="acme")
    member = await seed_user(
        db_session, org_id=org.id, email="m@acme.example", full_name="M", role=UserRole.member
    )
    service = _service(db_session)

    await service.update_user(
        org.id, member.id, UserUpdateRequest(role=UserRole.company_admin), _ACTOR
    )

    rows = await _audit_rows(db_session, "user.role_change")
    assert len(rows) == 1
    assert rows[0].entity_id == member.id
    assert rows[0].details == {"from_role": "member", "to_role": "company_admin"}


async def test_deactivate_user_emits_audit_row(db_session: AsyncSession) -> None:
    # Deactivating a member (not the last admin) records user.deactivate.
    org = await seed_organization(db_session, name="Acme", slug="acme")
    member = await seed_user(
        db_session, org_id=org.id, email="m@acme.example", full_name="M", role=UserRole.member
    )
    service = _service(db_session)

    await service.deactivate_user(org.id, member.id, _ACTOR)

    rows = await _audit_rows(db_session, "user.deactivate")
    assert len(rows) == 1
    assert rows[0].entity_id == member.id


async def test_last_admin_guard_serializes_concurrent_removals(db_session: AsyncSession) -> None:
    # DYN-01: two admins removed concurrently (separate transactions) must NOT both
    # succeed and strand the org at 0 admins. The guard locks the active-admin set
    # FOR UPDATE, so exactly one removal wins and the other gets LastAdminError.
    org = await seed_organization(db_session, name="Race", slug="race-dyn01")
    admin_one = await seed_user(
        db_session, org_id=org.id, email="a1@race.example", full_name="A1",
        role=UserRole.company_admin,
    )
    admin_two = await seed_user(
        db_session, org_id=org.id, email="a2@race.example", full_name="A2",
        role=UserRole.company_admin,
    )
    await db_session.commit()

    async def deactivate(user_id: object) -> str:
        async with scoped_session(org.id) as session:
            service = UserService(
                users=UserRepository(session), audit=AuditService(AuditRepository(session))
            )
            try:
                await service.deactivate_user(org.id, user_id, _ACTOR)
                await session.commit()
                return "ok"
            except LastAdminError:
                await session.rollback()
                return "blocked"

    outcomes = sorted(
        await asyncio.gather(deactivate(admin_one.id), deactivate(admin_two.id))
    )

    assert outcomes == ["blocked", "ok"]
