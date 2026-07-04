"""
Standing-invariant + live-enforcement tests for the least-privilege grant split (migration 0013).

Two layers of guarantee, mirroring tests/identity/models/test_rls_invariants.py:

  1. STANDING INVARIANT (catalog) — proves via pg_catalog that the tenant role `oneai_app` holds
     ZERO privileges on the platform plane (platform_admins, refresh_tokens, organizations,
     alembic_version), holds INSERT+SELECT but NOT UPDATE/DELETE on audit_log, that audit_log is
     ENABLE+FORCE RLS'd with the org_isolation policy, that every TenantMixin table KEEPS full DML
     (dynamically enumerated — a future tenant-table migration that forgets its now-mandatory
     explicit GRANT fails here, since 0013 revoked the 0009 default-ACL auto-grant), and that the
     default ACL no longer blanket-grants future tables to the tenant role.

  2. LIVE ENFORCEMENT — connects AS the runtime roles and proves the privileges/policies actually
     bite: a tenant-engine read of platform_admins and a tenant-engine UPDATE of audit_log are both
     permission-denied; a tenant-engine audit INSERT stamped with another org's id is rejected by
     WITH CHECK; an own-org INSERT succeeds and a filter-less tenant SELECT sees ONLY the GUC org
     (the cross-tenant negative test), while the BYPASSRLS global engine sees both rows (the TEETH).

Requires a migrated (alembic upgrade head, ≥0013) + role-provisioned DB; skips loudly otherwise.
Audit rows written here are removed by TRUNCATE on teardown (the trigger blocks DELETE; the
identity suite already TRUNCATEs audit_log the same way).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

# Importing each domain's model package registers its TenantMixin subclasses, so the dynamic
# tenant-table enumeration below covers EVERY domain (same idiom as test_rls_invariants).
import app.access.models  # noqa: F401 — registers access (PF-01) TenantMixin subclasses
import app.connectors.imap.models  # noqa: F401 — registers email Layer-1 TenantMixin subclasses
import app.connectors.models  # noqa: F401 — registers connector TenantMixin subclasses
import app.entities.models  # noqa: F401 — registers entity-graph TenantMixin subclasses
import app.identity.models  # noqa: F401 — registers identity TenantMixin subclasses
from app.common.base_model import TenantMixin
from app.core.config import get_settings
from app.core.database import engine, global_engine, runtime_roles_present, tenant_engine

_settings = get_settings()
_APP_ROLE = _settings.app_db_user
_GLOBAL_ROLE = _settings.global_db_user

# The platform plane stripped from the tenant role by migration 0013.
_PLATFORM_ONLY_TABLES = ["platform_admins", "refresh_tokens", "organizations", "alembic_version"]

_DML_PRIVILEGES = ["SELECT", "INSERT", "UPDATE", "DELETE"]

# Marker action for rows this module writes (TRUNCATE'd on teardown).
_SELFTEST_ACTION = "audit.least-privilege-selftest"


def _all_tenant_table_names() -> list[str]:
    """Recursively collect every TenantMixin-mapped table name (indirect subclasses included)."""
    names: list[str] = []
    pending = list(TenantMixin.__subclasses__())
    while pending:
        model = pending.pop()
        names.append(model.__table__.name)
        pending.extend(model.__subclasses__())
    return sorted(names)


async def _has_table_privilege(
    connection: AsyncConnection, role: str, table: str, privilege: str
) -> bool:
    """Return pg_catalog's verdict on whether `role` holds `privilege` on public.`table`."""
    result = await connection.execute(
        text("SELECT has_table_privilege(:role, :table, :privilege)"),
        {"role": role, "table": f"public.{table}", "privilege": privilege},
    )
    return bool(result.scalar())


@pytest_asyncio.fixture
async def migrated_db() -> AsyncIterator[None]:
    """Skip unless the DB is migrated AND the runtime roles exist (0013 is migration-only)."""
    if not await runtime_roles_present():
        pytest.skip(
            "Runtime DB roles missing — run `alembic upgrade head` then "
            "`python -m scripts.provision_roles` before the DB suite."
        )
    async with engine.connect() as connection:
        migrated = (
            await connection.execute(text("SELECT to_regclass('public.alembic_version')"))
        ).scalar() is not None
    if not migrated:
        pytest.skip("Grants are migration-only and this DB is not migrated (no alembic_version).")
    yield


@pytest_asyncio.fixture
async def clean_audit_rows(migrated_db: None) -> AsyncIterator[None]:
    """TRUNCATE audit_log after a test that writes rows (the append-only trigger blocks DELETE).

    CASCADE since PF-01 (0019): visibility_promotion FKs audit_log (the widening lineage
    anchor), so a plain TRUNCATE is refused; cascading also empties that table, which is
    correct test isolation.
    """
    yield
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE audit_log CASCADE"))


# — 1) Standing invariants (catalog, owner engine) —


async def test_tenant_role_platform_tables_all_privileges_revoked(migrated_db: None) -> None:
    """oneai_app must hold NO DML privilege on any platform-plane table (0013 item 1)."""
    async with engine.connect() as connection:
        held = [
            (table, privilege)
            for table in _PLATFORM_ONLY_TABLES
            for privilege in _DML_PRIVILEGES
            if await _has_table_privilege(connection, _APP_ROLE, table, privilege)
        ]

    assert held == [], f"tenant role {_APP_ROLE!r} still holds platform-plane privileges: {held}"


async def test_tenant_role_audit_log_privileges_append_only(migrated_db: None) -> None:
    """oneai_app keeps INSERT + SELECT (INSERT..RETURNING) on audit_log but NOT UPDATE/DELETE."""
    async with engine.connect() as connection:
        verdicts = {
            privilege: await _has_table_privilege(connection, _APP_ROLE, "audit_log", privilege)
            for privilege in _DML_PRIVILEGES
        }

    assert verdicts == {"SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": False}, (
        f"audit_log privileges for {_APP_ROLE!r} drifted from append-only: {verdicts}"
    )


async def test_audit_log_rls_enabled_forced_with_isolation_policy(migrated_db: None) -> None:
    """audit_log must be ENABLE + FORCE RLS'd and carry the org_isolation policy (0013 item 2)."""
    async with engine.connect() as connection:
        rls_row = (
            await connection.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relname = 'audit_log' AND relnamespace = 'public'::regnamespace"
                )
            )
        ).first()
        policy_present = (
            await connection.execute(
                text(
                    "SELECT 1 FROM pg_policies WHERE schemaname = 'public' "
                    "AND tablename = 'audit_log' AND policyname = 'org_isolation'"
                )
            )
        ).first() is not None

    assert rls_row is not None, "audit_log absent on a migrated DB — missing migration?"
    assert rls_row[0] is True, "audit_log: ROW LEVEL SECURITY is not ENABLED (0013)"
    assert rls_row[1] is True, "audit_log: FORCE ROW LEVEL SECURITY is not set (0013)"
    assert policy_present, "audit_log: missing the 'org_isolation' RLS policy (0013)"


# Deliberate per-table privilege narrowings on the tenant role — each documented in its
# migration. visibility_promotion (0019): append-only lineage — the no-UPDATE trigger plus a
# DELETE revoke, so tenant-plane code can neither rewrite nor erase a widening record (the GDPR
# erasure hook deletes on the BYPASS global role).
_TENANT_DML_EXCEPTIONS: dict[str, set[str]] = {"visibility_promotion": {"UPDATE", "DELETE"}}


async def test_every_tenant_table_retains_full_dml_for_tenant_role(migrated_db: None) -> None:
    """Every TenantMixin table must keep full DML for oneai_app (minus documented narrowings).

    Dual guard: 0013 must not have broken the 14 existing tenant tables, and — since 0013 revoked
    the default-ACL auto-grant — a FUTURE tenant-table migration that forgets its now-mandatory
    explicit GRANT to oneai_app fails here instead of 500ing at runtime. Tables in
    _TENANT_DML_EXCEPTIONS are checked BOTH ways: the remaining DML must be held AND the
    narrowed privileges must actually be absent (the narrowing is an invariant too).
    """
    tenant_tables = _all_tenant_table_names()
    assert tenant_tables, "TenantMixin enumeration is empty — models not imported (vacuous test)."

    async with engine.connect() as connection:
        missing = [
            (table, privilege)
            for table in tenant_tables
            for privilege in _DML_PRIVILEGES
            if privilege not in _TENANT_DML_EXCEPTIONS.get(table, set())
            and not await _has_table_privilege(connection, _APP_ROLE, table, privilege)
        ]
        wrongly_held = [
            (table, privilege)
            for table, narrowed in _TENANT_DML_EXCEPTIONS.items()
            for privilege in sorted(narrowed)
            if await _has_table_privilege(connection, _APP_ROLE, table, privilege)
        ]

    assert missing == [], (
        f"tenant tables missing DML for {_APP_ROLE!r} (forgot the explicit GRANT "
        f"required since migration 0013?): {missing}"
    )
    assert wrongly_held == [], (
        f"documented privilege narrowings not in force for {_APP_ROLE!r}: {wrongly_held}"
    )


async def test_default_acl_grants_no_future_tables_to_tenant_role(migrated_db: None) -> None:
    """The default table ACL must not auto-grant the tenant role (but keeps the global one)."""
    async with engine.connect() as connection:
        acl_rows = [
            row[0]
            for row in await connection.execute(
                text(
                    "SELECT d.defaclacl::text FROM pg_default_acl d "
                    "JOIN pg_namespace n ON n.oid = d.defaclnamespace "
                    "WHERE n.nspname = 'public' AND d.defaclobjtype = 'r'"
                )
            )
        ]

    assert not any(f"{_APP_ROLE}=" in acl for acl in acl_rows), (
        f"default table ACL still auto-grants future tables to {_APP_ROLE!r}: {acl_rows}"
    )
    assert any(f"{_GLOBAL_ROLE}=" in acl for acl in acl_rows), (
        f"default table ACL lost the deliberate {_GLOBAL_ROLE!r} auto-grant (0009 pattern): "
        f"{acl_rows}"
    )


# — 2) Live enforcement (runtime roles) —


async def test_tenant_engine_select_platform_admins_denied(migrated_db: None) -> None:
    """Connected AS oneai_app, reading platform credentials is a hard permission error."""
    with pytest.raises(DBAPIError) as raised:
        async with tenant_engine.connect() as connection:
            await connection.execute(text("SELECT count(*) FROM platform_admins"))

    assert "permission denied" in str(raised.value).lower()


async def test_tenant_engine_update_audit_log_denied(migrated_db: None) -> None:
    """Connected AS oneai_app, an audit_log UPDATE dies at the privilege layer (pre-trigger)."""
    with pytest.raises(DBAPIError) as raised:
        async with tenant_engine.connect() as connection:
            await connection.execute(text("UPDATE audit_log SET action = 'tampered'"))

    assert "permission denied" in str(raised.value).lower()


async def _tenant_insert_audit_row(
    connection: AsyncConnection, guc_org: UUID, row_org: UUID
) -> None:
    """On an open tenant-engine transaction, pin the GUC to `guc_org` and insert a `row_org` row."""
    await connection.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(guc_org)}
    )
    await connection.execute(
        text("INSERT INTO audit_log (actor_type, action, org_id) VALUES ('system', :action, :org)"),
        {"action": _SELFTEST_ACTION, "org": str(row_org)},
    )


async def test_audit_log_tenant_insert_cross_org_rejected(clean_audit_rows: None) -> None:
    """A tenant-engine audit INSERT stamped with ANOTHER org's id must fail WITH CHECK."""
    org_a, org_b = uuid4(), uuid4()

    with pytest.raises(DBAPIError) as raised:
        async with tenant_engine.connect() as connection:
            async with connection.begin():
                await _tenant_insert_audit_row(connection, guc_org=org_a, row_org=org_b)

    assert "row-level security" in str(raised.value).lower()


async def test_audit_log_tenant_select_sees_only_guc_org(clean_audit_rows: None) -> None:
    """Own-org tenant INSERT succeeds; a filter-less tenant SELECT sees ONLY the GUC org's rows.

    Seeds another org's row via the BYPASSRLS global engine (the platform plane legitimately writes
    cross-org), then asserts the tenant engine cannot see it — the cross-tenant negative read — and
    that the global engine sees BOTH rows, proving the tenant assertion is not vacuously green.
    """
    org_a, org_b = uuid4(), uuid4()
    async with global_engine.connect() as connection:
        async with connection.begin():
            await connection.execute(
                text(
                    "INSERT INTO audit_log (actor_type, action, org_id) "
                    "VALUES ('system', :action, :org)"
                ),
                {"action": _SELFTEST_ACTION, "org": str(org_b)},
            )

    async with tenant_engine.connect() as connection:
        async with connection.begin():
            await _tenant_insert_audit_row(connection, guc_org=org_a, row_org=org_a)
            tenant_visible = {
                row[0]
                for row in await connection.execute(
                    text("SELECT org_id FROM audit_log WHERE action = :action"),
                    {"action": _SELFTEST_ACTION},
                )
            }
    async with global_engine.connect() as connection:
        global_visible = {
            row[0]
            for row in await connection.execute(
                text("SELECT org_id FROM audit_log WHERE action = :action"),
                {"action": _SELFTEST_ACTION},
            )
        }

    assert tenant_visible == {org_a}, f"tenant engine leaked foreign audit rows: {tenant_visible}"
    assert global_visible == {org_a, org_b}, (
        "global (BYPASSRLS) engine must see both rows — otherwise the tenant assertion is vacuous"
    )
