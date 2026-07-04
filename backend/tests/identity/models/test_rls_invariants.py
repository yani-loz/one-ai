"""
Standing-invariant + live-enforcement tests for tenant Row-Level Security (security.md layer 2).

Two layers of guarantee:

  1. STANDING INVARIANT (catalog) — proves, by DYNAMIC enumeration (not a hardcoded list), that
     every ORM model mixing in TenantMixin has its table protected by ENABLE + FORCE ROW LEVEL
     SECURITY + an `org_isolation` policy. A future tenant table that forgets any of the three fails
     HERE instead of silently shipping cross-org-readable. Read on the OWNER engine (catalog reads
     need no runtime role), and SKIPPED only on a genuinely non-migrated DB — keyed on the
     migration-independent `alembic_version` table, NOT on a sentinel table's own policy (so a
     forgotten policy on ANY tenant table, including the former sentinel `users`, FAILs not SKIPs —
     closes audit finding TC-SG-015).

  2. LIVE ENFORCEMENT (rows) — proves the policies actually FILTER at runtime now that the app
     connects as the non-bypass `oneai_app` role (migration 0009 + scripts.provision_roles). Seeds
     two orgs, then asserts the tenant engine sees only its own org while the BYPASSRLS global
     engine sees both — that second assertion is the TEETH: it shows the test distinguishes
     enforced from bypassed, so a vacuously-green "isolation" run (a tenant query accidentally on
     the bypass pool) is impossible. Also asserts a cross-org WRITE is rejected by WITH CHECK.
     These tests require a migrated + role-provisioned DB; identity_schema skips loudly otherwise.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

# Importing each domain's model package registers its TenantMixin subclasses, so the dynamic
# enumeration below covers EVERY domain's tenant tables (a tenant table whose model isn't imported
# here would be invisible to this safety net).
import app.access.models  # noqa: E402, F401 — registers access (PF-01) TenantMixin subclasses
import app.connectors.imap.models  # noqa: E402, F401 — registers email Layer-1 TenantMixin subclasses
import app.connectors.models  # noqa: E402, F401 — registers connector TenantMixin subclasses
import app.entities.models  # noqa: E402, F401 — registers entity-graph TenantMixin subclasses
from app.common.base_model import TenantMixin, VisibilityScopedMixin
from app.connectors.imap.models.email import EmailMessage
from app.connectors.models.connector_connection import ConnectorConnection
from app.core.database import engine, global_engine, tenant_engine
from app.entities.models.person import Person
from app.identity import models as identity_models  # noqa: F401 — registers TenantMixin subclasses
from app.identity.models.organization import Organization
from app.identity.models.support_grant import SupportGrant
from app.identity.models.user import User
from app.identity.security.password import hash_password

# Tables that carry org-related data but are DELIBERATELY not tenant-RLS-scoped: the
# content-blind platform/compliance plane (audit_log, organizations, platform_admins) plus
# subject-keyed refresh_tokens. A TenantMixin mix-in on any of these is a design break the
# enumeration test must catch.
_KNOWN_NON_TENANT_TABLES = {
    "audit_log",
    "organizations",
    "platform_admins",
    "refresh_tokens",
}

_ISOLATION_POLICY = "org_isolation"

# Live-proof seed identity (cleaned up by the identity_schema fixture's TRUNCATE on teardown).
_ORG_A_EMAIL = "rls-iso-a@example.test"
_ORG_B_EMAIL = "rls-iso-b@example.test"


def _all_tenant_models() -> list[type]:
    """Recursively collect every mapped model that mixes in TenantMixin.

    Recurses so an indirect subclass (a tenant model inheriting another tenant model) is
    still caught. Returns the mapped classes; read their table name via ``__table__.name``.
    """
    discovered: list[type] = []
    pending = list(TenantMixin.__subclasses__())
    while pending:
        model = pending.pop()
        discovered.append(model)
        pending.extend(model.__subclasses__())
    return discovered


async def _policy_exists(
    connection: AsyncConnection, table: str, policy: str = _ISOLATION_POLICY
) -> bool:
    """Return True if `policy` is defined on public.`table` (pg_policies catalog view)."""
    result = await connection.execute(
        text(
            "SELECT 1 FROM pg_policies "
            "WHERE schemaname = 'public' AND tablename = :table AND policyname = :policy"
        ),
        {"table": table, "policy": policy},
    )
    return result.first() is not None


async def _is_migrated(connection: AsyncConnection) -> bool:
    """Return True if this is an Alembic-migrated DB (the `alembic_version` table exists).

    Migration-independent skip oracle: catalog RLS policies live only in the migrations, so a fresh
    create_all DB has neither them nor `alembic_version`. Keying the skip on that table (not on a
    sentinel table's policy) means a forgotten policy on ANY tenant table FAILs the test
    rather than being misread as "not migrated" and SKIPped (closes TC-SG-015).
    """
    result = await connection.execute(text("SELECT to_regclass('public.alembic_version')"))
    return result.scalar() is not None


def test_tenant_model_enumeration_is_non_vacuous_and_content_blind() -> None:
    """The dynamic enumeration must neither collapse to empty nor pick up a platform table.

    Guards the DB-level invariant below: if models aren't imported, ``__subclasses__()`` is
    empty and the per-table assertions would pass vacuously. And a stray TenantMixin on a
    content-blind table (audit_log/organizations/platform_admins/refresh_tokens) would break
    the platform plane's cross-org reach — so assert those stay out of the tenant set.
    """
    tenant_tables = {model.__table__.name for model in _all_tenant_models()}

    assert tenant_tables, (
        "TenantMixin enumeration is empty — identity models not imported; the RLS "
        "invariant below would pass vacuously."
    )
    assert {
        User.__table__.name,
        SupportGrant.__table__.name,
        ConnectorConnection.__table__.name,
        Person.__table__.name,
        EmailMessage.__table__.name,
    } <= tenant_tables
    assert tenant_tables.isdisjoint(_KNOWN_NON_TENANT_TABLES), (
        f"A non-tenant platform table mixed in TenantMixin: "
        f"{tenant_tables & _KNOWN_NON_TENANT_TABLES}"
    )


async def test_every_tenant_table_has_rls_enabled_forced_and_isolation_policy() -> None:
    """Every TenantMixin table must have RLS ENABLED + FORCED + an org_isolation policy.

    Dynamically enumerated (see module docstring) so adding a tenant table without its RLS policy or
    FORCE fails here. Read on the OWNER engine (catalog reads need no runtime role). Skips only on a
    non-migrated DB, keyed on `alembic_version` (not a sentinel policy) so a forgotten policy on any
    table — including `users` — FAILs rather than SKIPs.
    """
    tenant_tables = sorted(model.__table__.name for model in _all_tenant_models())

    async with engine.connect() as connection:
        if not await _is_migrated(connection):
            pytest.skip(
                "RLS policies are migration-only and this DB is not migrated (no alembic_version). "
                "Run against a migrated DB (alembic upgrade head)."
            )

        for table in tenant_tables:
            row = (
                await connection.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE relname = :table AND relnamespace = 'public'::regnamespace"
                    ),
                    {"table": table},
                )
            ).first()

            assert row is not None, (
                f"{table}: tenant table absent on a migrated DB — missing migration?"
            )
            relrowsecurity, relforcerowsecurity = row
            assert relrowsecurity is True, f"{table}: ROW LEVEL SECURITY is not ENABLED"
            assert relforcerowsecurity is True, (
                f"{table}: FORCE ROW LEVEL SECURITY is not set (migration 0009 flip)"
            )
            assert await _policy_exists(connection, table), (
                f"{table}: missing the {_ISOLATION_POLICY!r} RLS policy"
            )


def _all_visibility_scoped_models() -> list[type]:
    """Every mapped CONTENT model (VisibilityScopedMixin) — the PF-01 AC1 enumeration."""
    discovered: list[type] = []
    pending = list(VisibilityScopedMixin.__subclasses__())
    while pending:
        model = pending.pop()
        discovered.append(model)
        pending.extend(model.__subclasses__())
    return discovered


def test_content_model_enumeration_is_non_vacuous() -> None:
    """The AC1 content enumeration must find the email Layer-1 tables (vacuity guard)."""
    content_tables = {model.__table__.name for model in _all_visibility_scoped_models()}

    assert {"email_message", "email_recipient", "email_attachment"} <= content_tables


async def test_every_content_table_has_org_isolation_and_visibility_policy() -> None:
    """PF-01 AC1: every content table carries BOTH policies — org_isolation AND a RESTRICTIVE
    `visibility` policy FOR SELECT. A content table without a visibility policy is a CI FAIL,
    never a SKIP (the retrieval layer must never be able to exist person-unscoped).
    """
    content_tables = sorted(model.__table__.name for model in _all_visibility_scoped_models())

    async with engine.connect() as connection:
        if not await _is_migrated(connection):
            pytest.skip(
                "RLS policies are migration-only and this DB is not migrated (no alembic_version)."
            )
        for table in content_tables:
            assert await _policy_exists(connection, table), (
                f"{table}: missing the {_ISOLATION_POLICY!r} policy (content tables carry BOTH)"
            )
            row = (
                await connection.execute(
                    text(
                        "SELECT permissive, cmd FROM pg_policies "
                        "WHERE schemaname = 'public' AND tablename = :table "
                        "AND policyname = 'visibility'"
                    ),
                    {"table": table},
                )
            ).first()
            assert row is not None, (
                f"{table}: missing the 'visibility' RLS policy (PF-01 AC1 — a content table "
                "must never exist person-unscoped)"
            )
            permissive, cmd = row
            assert permissive == "RESTRICTIVE", (
                f"{table}: 'visibility' must be RESTRICTIVE (ANDed with org_isolation) — a "
                f"permissive policy would WIDEN access, got {permissive!r}"
            )
            assert cmd == "SELECT", f"{table}: 'visibility' must gate SELECT, got {cmd!r}"


async def _seed_two_orgs(session: AsyncSession) -> tuple[UUID, UUID]:
    """Seed two orgs (one user each) via the BYPASSRLS session, committed for other connections."""
    org_a = Organization(name="RLS Org A", slug="rls-iso-a", status="active")
    org_b = Organization(name="RLS Org B", slug="rls-iso-b", status="active")
    session.add_all([org_a, org_b])
    await session.flush()
    session.add_all(
        [
            User(
                org_id=org_a.id,
                email=_ORG_A_EMAIL,
                full_name="Org A User",
                password_hash=hash_password("Pw-Iso-123456"),
                role="member",
                is_active=True,
            ),
            User(
                org_id=org_b.id,
                email=_ORG_B_EMAIL,
                full_name="Org B User",
                password_hash=hash_password("Pw-Iso-123456"),
                role="member",
                is_active=True,
            ),
        ]
    )
    await session.commit()
    return org_a.id, org_b.id


async def _visible_org_ids(db_engine: AsyncEngine, *, scoped_to: UUID) -> set[UUID]:
    """Return the seeded users' org_ids visible to `db_engine` with the GUC pinned to `scoped_to`.

    One transaction so the transaction-local `app.current_org_id` covers the SELECT. The email
    filter is two internal test constants (no injection surface), kept narrow so the result reflects
    only this test's seed.
    """
    async with db_engine.connect() as connection:
        async with connection.begin():
            await connection.execute(
                text("SELECT set_config('app.current_org_id', :org, true)"),
                {"org": str(scoped_to)},
            )
            result = await connection.execute(
                text(
                    "SELECT DISTINCT org_id FROM users "
                    f"WHERE email IN ('{_ORG_A_EMAIL}', '{_ORG_B_EMAIL}')"
                )
            )
            return {row[0] for row in result}


async def test_rls_enforces_cross_org_isolation(db_session: AsyncSession) -> None:
    """The tenant engine sees only its own org; the global (BYPASSRLS) engine sees both.

    The global-engine assertion is the TEETH: it returns BOTH orgs even with the GUC pinned to one,
    so the tenant-engine assertions ("only my org") are meaningful — were RLS inert, or were the
    tenant query accidentally run on the bypass pool, the tenant assertions would see both and FAIL.
    """
    org_a, org_b = await _seed_two_orgs(db_session)

    # oneai_app (non-bypass): RLS filters to exactly the GUC-pinned org, both directions.
    assert await _visible_org_ids(tenant_engine, scoped_to=org_a) == {org_a}
    assert await _visible_org_ids(tenant_engine, scoped_to=org_b) == {org_b}

    # oneai_global (BYPASSRLS): the SAME query, same GUC, sees BOTH orgs — proves the test above is
    # not vacuously green (enforced vs bypassed are distinguishable).
    assert await _visible_org_ids(global_engine, scoped_to=org_a) == {org_a, org_b}


async def test_rls_rejects_cross_org_write(db_session: AsyncSession) -> None:
    """A WITH CHECK violation (moving a row to another org) is rejected on the tenant engine.

    As oneai_app scoped to org A, updating its own user's org_id to org B must fail with a
    row-level-security error — proving the policy guards writes, not just reads.
    """
    org_a, org_b = await _seed_two_orgs(db_session)

    with pytest.raises(DBAPIError) as raised:
        async with tenant_engine.connect() as connection:
            async with connection.begin():
                await connection.execute(
                    text("SELECT set_config('app.current_org_id', :org, true)"),
                    {"org": str(org_a)},
                )
                await connection.execute(
                    text("UPDATE users SET org_id = :b WHERE org_id = :a"),
                    {"a": str(org_a), "b": str(org_b)},
                )

    assert "row-level security" in str(raised.value).lower()
