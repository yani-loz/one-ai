"""
Standing-invariant tests for tenant Row-Level Security (security.md layer 2).

Proves — by DYNAMIC enumeration, not a hardcoded {users, support_grant} list — that every
ORM model mixing in TenantMixin has its table protected by ENABLE ROW LEVEL SECURITY + an
`org_isolation` policy (migrations 0003 + 0006). A future tenant table that forgets its policy
fails HERE instead of silently shipping cross-org-readable. Two guards keep this from rotting
into a silent no-op: the enumeration must be non-empty AND must not sweep in a non-tenant
platform table (a stray mix-in). FORCE ROW LEVEL SECURITY is intentionally NOT asserted yet —
that switch, plus the non-superuser runtime role, lands with the RLS role/engine flip (its own
later migration; 0007 is connector_connection); see docs/rls-jwt-enforcement-plan.md.

Requires a MIGRATED database: the policies live only in the Alembic migrations (not in the
models / create_all). On a fresh create_all DB the policies are absent, so the DB-level test
skips loudly rather than failing — CI and the dev container both run `alembic upgrade head`
first, so the test has full teeth where it actually runs.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Importing each domain's model package registers its TenantMixin subclasses, so the dynamic
# enumeration below covers EVERY domain's tenant tables (a tenant table whose model isn't imported
# here would be invisible to this safety net).
import app.connectors.imap.models  # noqa: E402, F401 — registers email Layer-1 TenantMixin subclasses
import app.connectors.models  # noqa: E402, F401 — registers connector TenantMixin subclasses
import app.entities.models  # noqa: E402, F401 — registers entity-graph TenantMixin subclasses
from app.common.base_model import TenantMixin
from app.connectors.imap.models.email import EmailMessage
from app.connectors.models.connector_connection import ConnectorConnection
from app.core.database import SessionLocal
from app.entities.models.person import Person
from app.identity import models as identity_models  # noqa: F401 — registers TenantMixin subclasses
from app.identity.models.support_grant import SupportGrant
from app.identity.models.user import User

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

# Migration 0003 attaches org_isolation to `users`; its absence ⇒ a non-migrated DB ⇒ skip.
_SENTINEL_TABLE = "users"
_ISOLATION_POLICY = "org_isolation"


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
    session: AsyncSession, table: str, policy: str = _ISOLATION_POLICY
) -> bool:
    """Return True if `policy` is defined on public.`table` (pg_policies catalog view)."""
    result = await session.execute(
        text(
            "SELECT 1 FROM pg_policies "
            "WHERE schemaname = 'public' AND tablename = :table AND policyname = :policy"
        ),
        {"table": table, "policy": policy},
    )
    return result.first() is not None


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


async def test_every_tenant_table_has_rls_enabled_and_isolation_policy() -> None:
    """Every TenantMixin table must have RLS ENABLED + an org_isolation policy.

    Dynamically enumerated (see module docstring) so adding a tenant table without its RLS
    policy fails here. Skips on a non-migrated DB where the migration-only policies are absent.
    FORCE is not asserted yet — it lands with the RLS role/engine flip (a later migration).
    """
    tenant_tables = sorted(model.__table__.name for model in _all_tenant_models())

    async with SessionLocal() as session:
        if not await _policy_exists(session, _SENTINEL_TABLE):
            pytest.skip(
                "RLS policies are migration-only and absent on this database "
                "(fresh create_all DB). Run against a migrated DB (alembic upgrade head)."
            )

        for table in tenant_tables:
            enabled = await session.execute(
                text(
                    "SELECT relrowsecurity FROM pg_class "
                    "WHERE relname = :table AND relnamespace = 'public'::regnamespace"
                ),
                {"table": table},
            )
            relrowsecurity = enabled.scalar_one_or_none()

            assert relrowsecurity is not None, (
                f"{table}: tenant table absent on a migrated DB — missing migration?"
            )
            assert relrowsecurity is True, f"{table}: ROW LEVEL SECURITY is not ENABLED"
            assert await _policy_exists(session, table), (
                f"{table}: missing the {_ISOLATION_POLICY!r} RLS policy"
            )
