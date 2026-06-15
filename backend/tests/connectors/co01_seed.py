"""
Role: Committed DB-seed helpers for the CO-01 connector-authorization route tests — register an
      org, seed a user (the owner/override/consent composite-FK target), and pre-set the three
      permission inputs (entitlement / org-wide policy / per-user override) directly in the DB so a
      route test can arrange a precise authorization state before it calls /me or /admin/connectors.
Used by: tests/connectors/test_me_connector_routes.py, test_connector_governance_routes.py,
         test_connector_entitlement_routes.py (and any CO-01 service/repo test wanting real rows).
Depends on: app.connectors models (entitlement/policy/override) + enums, app.identity.models.user +
            security.password (bcrypt), app.core.database (GlobalSessionLocal — committed seed),
            tests.conftest.register_org (the 0014 org-root FK).
Key invariants:
  - Every helper COMMITS on its own GlobalSessionLocal session so a later HTTP request (which opens
    its own session) sees the row — the seed is visible cross-session, like seed_org.
  - entitlement is the PLATFORM plane (no TenantMixin): seeded with a plain org_id column, read by
    the global session at request time. policy/override are tenant rows but seeding them on the
    global (BYPASSRLS) session is fine — RLS only gates the request-time tenant session.
  - seed_user derives a unique email from the user UUID so parallel orgs never collide on the global
    uq_users_email_lower index. The owner FK (org_id, user_id) -> users(org_id, id) requires this
    user to exist before a self-connect or override/consent insert.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.connectors.enums import OverrideType
from app.connectors.models.connector_entitlement import ConnectorEntitlement
from app.connectors.models.connector_policy import ConnectorPolicy
from app.connectors.models.connector_policy_override import ConnectorPolicyOverride
from app.core.database import GlobalSessionLocal
from app.identity.models.user import User
from app.identity.security.password import hash_password
from tests.conftest import register_org, seed_org


async def seed_entitled_org(*, connector_type: str = "imap") -> UUID:
    """Register an org (seed_org) AND grant it the connector entitlement; return the org_id.

    For admin-plane route tests that exercise the connector lifecycle (create/test/sync) — which
    now enforce the CO-01 Tier-1 entitlement ceiling — so each test doesn't have to arrange
    entitlement by hand. A non-entitled admin case is tested explicitly elsewhere.
    """
    org_id = await seed_org()
    await seed_entitlement(org_id, enabled=True, connector_type=connector_type)
    return org_id


async def seed_user(org_id: UUID, *, user_id: UUID | None = None, role: str = "member") -> UUID:
    """Insert (and COMMIT) a user in `org_id` and return its id (the owner/override FK target).

    Registers the org first (idempotent, 0014 FK). The email is derived from the user UUID so it is
    globally unique (uq_users_email_lower) across parallel orgs. Default role 'member' — pass
    'company_admin' for an admin user.
    """
    uid = user_id if user_id is not None else uuid4()
    async with GlobalSessionLocal() as session:
        await register_org(session, org_id)
        session.add(
            User(
                id=uid,
                org_id=org_id,
                email=f"user-{uid.hex}@example.com",
                full_name=f"Test User {uid.hex[:8]}",
                password_hash=hash_password("Test-Pass-123"),
                role=role,
            )
        )
        await session.commit()
    return uid


async def seed_entitlement(
    org_id: UUID, *, enabled: bool = True, connector_type: str = "imap"
) -> None:
    """Insert (and COMMIT) a platform-plane entitlement row for `org_id` (the Tier-1 ceiling)."""
    async with GlobalSessionLocal() as session:
        await register_org(session, org_id)
        session.add(
            ConnectorEntitlement(org_id=org_id, connector_type=connector_type, enabled=enabled)
        )
        await session.commit()


async def seed_policy(
    org_id: UUID, *, org_wide_enabled: bool, connector_type: str = "imap"
) -> None:
    """Insert (and COMMIT) the org-wide policy row for `org_id` (the Tier-2 default reach)."""
    async with GlobalSessionLocal() as session:
        await register_org(session, org_id)
        session.add(
            ConnectorPolicy(
                org_id=org_id,
                connector_type=connector_type,
                org_wide_enabled=org_wide_enabled,
            )
        )
        await session.commit()


async def seed_override(
    org_id: UUID, user_id: UUID, *, override_type: OverrideType, connector_type: str = "imap"
) -> None:
    """Insert (and COMMIT) a per-user grant/deny override (Tier-2; `user_id` must already exist)."""
    async with GlobalSessionLocal() as session:
        await register_org(session, org_id)
        session.add(
            ConnectorPolicyOverride(
                org_id=org_id,
                user_id=user_id,
                connector_type=connector_type,
                override_type=override_type.value,
            )
        )
        await session.commit()
