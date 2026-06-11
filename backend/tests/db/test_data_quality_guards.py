"""
Standing-invariant + live-enforcement tests for the data-quality guards (migration 0014).

Two layers of guarantee, mirroring tests/db/test_least_privilege_grants.py:

  1. STANDING INVARIANT (catalog) — proves via pg_catalog/information_schema that the audit's §4
     hardening plan landed exactly: org-root FKs on 13/15 org_id tables (12 new + users; audit_log
     + support_grant FK-free BY DESIGN) with NO ACTION on delete; UNIQUE lower(email) indexes on
     users/platform_admins; the 12 composite tenant-coherent FKs present with their predecessors'
     exact ON DELETE semantics and the superseded single-column FKs gone; the 4 parent
     UNIQUE (org_id, id) anchors; the recipient-edge UNIQUE; the sync-ledger guards; and the
     value-shape CHECKs.

  2. LIVE ENFORCEMENT — proves the guards actually bite: a tenant insert under an unregistered
     org dies on the org-root FK (the H-2 phantom-tenant regression), a child row stamped with a
     DIFFERENT org than its parent dies on the composite FK (the cross-tenant negative test), a
     legal revoked-from-requested support grant (decided_* all NULL — revoke never stamps a
     decision) is ACCEPTED, and an approved grant missing its time box is REJECTED.

Requires a migrated DB (alembic upgrade head, ≥0014); skips loudly otherwise. Live tests run on
the OWNER engine (schema enforcement is role-independent) inside transactions that are rolled
back or aborted by the expected violation — nothing persists.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.database import engine

# The 12 tables whose org-root FK migration 0014 added; users got fk_users_org_id in 0002.
_NEW_ORG_FK_TABLES = [
    "company",
    "company_domain",
    "connector_connection",
    "connector_sync_cursor",
    "connector_sync_run",
    "email_attachment",
    "email_message",
    "email_recipient",
    "person",
    "person_alias",
    "person_company",
    "person_email",
]

# org_id tables deliberately left WITHOUT an org FK: durable compliance attribution must survive
# org deletion (0005/0006 design, re-affirmed by the 2026-06-10 audit's by-design registry B-5).
_ORG_FK_EXEMPT_BY_DESIGN = {"audit_log", "support_grant"}

# (child, column, parent, expected ON DELETE phrase). The composite FK is named
# fk_<child>_<column>_org; the superseded single-column FK was <child>_<column>_fkey.
_COMPOSITE_FKS = [
    ("email_recipient", "email_id", "email_message", "ON DELETE CASCADE"),
    ("email_attachment", "email_id", "email_message", "ON DELETE CASCADE"),
    ("person_email", "person_id", "person", "ON DELETE CASCADE"),
    ("person_alias", "person_id", "person", "ON DELETE CASCADE"),
    ("person_company", "person_id", "person", "ON DELETE CASCADE"),
    ("person_company", "company_id", "company", "ON DELETE CASCADE"),
    ("company_domain", "company_id", "company", "ON DELETE CASCADE"),
    ("email_message", "connection_id", "connector_connection", "ON DELETE CASCADE"),
    ("connector_sync_run", "connection_id", "connector_connection", "ON DELETE CASCADE"),
    ("connector_sync_cursor", "connection_id", "connector_connection", "ON DELETE CASCADE"),
    ("email_message", "from_person_id", "person", "ON DELETE SET NULL (from_person_id)"),
    ("email_recipient", "person_id", "person", "ON DELETE SET NULL (person_id)"),
]

_COMPOSITE_FK_PARENTS = ["email_message", "person", "company", "connector_connection"]

# (constraint name, table) for every CHECK migration 0014 added.
_EXPECTED_CHECKS = [
    ("ck_sync_run_time_order", "connector_sync_run"),
    ("ck_sync_run_terminal_finished", "connector_sync_run"),
    ("ck_sync_run_counts_nonneg", "connector_sync_run"),
    ("ck_sync_cursor_uidvalidity_positive", "connector_sync_cursor"),
    ("ck_organizations_slug_format", "organizations"),
    ("ck_person_email_source", "person_email"),
    ("ck_person_alias_source", "person_alias"),
    ("ck_company_domain_source", "company_domain"),
    ("ck_refresh_tokens_hash_shape", "refresh_tokens"),
    ("ck_email_attachment_filename_nonempty", "email_attachment"),
    ("ck_support_grant_lifecycle", "support_grant"),
]


async def _constraint_definition(connection: AsyncConnection, table: str, name: str) -> str | None:
    """Return pg_get_constraintdef() for `name` on public.`table`, or None if absent."""
    result = await connection.execute(
        text(
            "SELECT pg_get_constraintdef(con.oid) FROM pg_constraint con "
            "JOIN pg_class rel ON rel.oid = con.conrelid "
            "WHERE rel.relname = :table AND rel.relnamespace = 'public'::regnamespace "
            "AND con.conname = :name"
        ),
        {"table": table, "name": name},
    )
    return result.scalar_one_or_none()


@pytest_asyncio.fixture
async def migrated_db() -> AsyncIterator[None]:
    """Skip unless the DB is migrated (the 0014 guards are migration-only DDL)."""
    async with engine.connect() as connection:
        migrated = (
            await connection.execute(text("SELECT to_regclass('public.alembic_version')"))
        ).scalar() is not None
    if not migrated:
        pytest.skip("Guards are migration-only and this DB is not migrated (no alembic_version).")
    yield


# — 1) Standing invariants (catalog, owner engine) —


async def test_org_root_fks_cover_all_org_tables_except_by_design_exempt(
    migrated_db: None,
) -> None:
    """Every org_id table except audit_log/support_grant must FK to organizations (13/15)."""
    async with engine.connect() as connection:
        org_tables = {
            row[0]
            for row in await connection.execute(
                text(
                    "SELECT c.table_name FROM information_schema.columns c "
                    "JOIN information_schema.tables t ON t.table_schema = c.table_schema "
                    "AND t.table_name = c.table_name AND t.table_type = 'BASE TABLE' "
                    "WHERE c.table_schema = 'public' AND c.column_name = 'org_id'"
                )
            )
        }
        fk_guarded = {
            row[0]
            for row in await connection.execute(
                text(
                    "SELECT src.relname FROM pg_constraint con "
                    "JOIN pg_class src ON src.oid = con.conrelid "
                    "JOIN pg_class tgt ON tgt.oid = con.confrelid "
                    "WHERE con.contype = 'f' AND tgt.relname = 'organizations'"
                )
            )
        }

    assert set(_NEW_ORG_FK_TABLES) | {"users"} <= fk_guarded, (
        f"org-root FKs missing on: {(set(_NEW_ORG_FK_TABLES) | {'users'}) - fk_guarded}"
    )
    assert org_tables - fk_guarded == _ORG_FK_EXEMPT_BY_DESIGN, (
        "org-FK coverage drifted — every org_id table except the two by-design exemptions "
        f"must be guarded; unguarded: {org_tables - fk_guarded}"
    )


async def test_org_root_fks_on_delete_no_action_never_cascade(migrated_db: None) -> None:
    """The new org-root FKs must be NO ACTION — an org DELETE must never cascade tenant data."""
    async with engine.connect() as connection:
        definitions = {
            table: await _constraint_definition(connection, table, f"fk_{table}_org_id")
            for table in _NEW_ORG_FK_TABLES
        }

    cascading = {t: d for t, d in definitions.items() if d is None or "ON DELETE" in d}
    assert cascading == {}, f"org-root FKs missing or carrying an ON DELETE action: {cascading}"


async def test_identity_email_lower_indexes_unique(migrated_db: None) -> None:
    """users + platform_admins must carry a UNIQUE functional index on lower(email) (M-1)."""
    expected = {"uq_users_email_lower", "uq_platform_admins_email_lower"}

    async with engine.connect() as connection:
        definitions = {
            row[0]: row[1]
            for row in await connection.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' AND indexname = ANY(:names)"
                ),
                {"names": list(expected)},
            )
        }

    assert set(definitions) == expected, (
        f"lower(email) indexes missing: {expected - set(definitions)}"
    )
    not_functional_unique = {
        name: definition
        for name, definition in definitions.items()
        if "CREATE UNIQUE INDEX" not in definition or "lower(" not in definition
    }
    assert not_functional_unique == {}, (
        f"lower(email) indexes are not UNIQUE-on-lower(email): {not_functional_unique}"
    )


async def test_composite_tenant_fks_replace_originals_preserving_on_delete(
    migrated_db: None,
) -> None:
    """All 12 composite (org_id, parent_id) FKs exist with the original ON DELETE semantics,
    and the superseded single-column FKs are gone (M-3, L-10)."""
    async with engine.connect() as connection:
        problems: list[str] = []
        for child, column, parent, ondelete in _COMPOSITE_FKS:
            definition = await _constraint_definition(connection, child, f"fk_{child}_{column}_org")
            if definition is None:
                problems.append(f"{child}: fk_{child}_{column}_org MISSING")
                continue
            if f"FOREIGN KEY (org_id, {column}) REFERENCES {parent}(org_id, id)" not in definition:
                problems.append(f"{child}.{column}: wrong shape: {definition}")
            if ondelete not in definition:
                problems.append(f"{child}.{column}: wrong ON DELETE: {definition}")
            superseded = await _constraint_definition(connection, child, f"{child}_{column}_fkey")
            if superseded is not None:
                problems.append(f"{child}: superseded {child}_{column}_fkey still present")

    assert problems == [], f"composite tenant-FK drift: {problems}"


async def test_composite_fk_parents_have_org_row_unique(migrated_db: None) -> None:
    """Each composite-FK parent must carry the UNIQUE (org_id, id) anchor constraint."""
    async with engine.connect() as connection:
        definitions = {
            parent: await _constraint_definition(connection, parent, f"uq_{parent}_org_row")
            for parent in _COMPOSITE_FK_PARENTS
        }

    wrong = {p: d for p, d in definitions.items() if d != "UNIQUE (org_id, id)"}
    assert wrong == {}, f"parent UNIQUE (org_id, id) anchors missing/wrong: {wrong}"


async def test_email_recipient_edge_unique_exists(migrated_db: None) -> None:
    """email_recipient must pin one edge per (email_id, kind, address) (M-6 guard)."""
    async with engine.connect() as connection:
        definition = await _constraint_definition(
            connection, "email_recipient", "uq_email_recipient_edge"
        )

    assert definition == "UNIQUE (email_id, kind, address)", (
        f"uq_email_recipient_edge missing or wrong: {definition}"
    )


async def test_sync_run_fencing_unique_exists(migrated_db: None) -> None:
    """connector_sync_run must pin the fencing token: UNIQUE (org_id, run_id) (L-4)."""
    async with engine.connect() as connection:
        definition = await _constraint_definition(
            connection, "connector_sync_run", "uq_sync_run_fencing"
        )

    assert definition == "UNIQUE (org_id, run_id)", (
        f"uq_sync_run_fencing missing or wrong: {definition}"
    )


async def test_value_shape_and_ledger_checks_exist(migrated_db: None) -> None:
    """Every CHECK constraint migration 0014 added must exist on its table."""
    async with engine.connect() as connection:
        missing = [
            (name, table)
            for name, table in _EXPECTED_CHECKS
            if await _constraint_definition(connection, table, name) is None
        ]

    assert missing == [], f"0014 CHECK constraints missing: {missing}"


# — 2) Live enforcement (owner engine; transactions never commit) —


async def test_person_insert_unregistered_org_rejected(migrated_db: None) -> None:
    """A tenant row stamped with an org_id that has NO organizations row must die on the
    org-root FK — the H-2 phantom-tenant regression test."""
    phantom_org = uuid4()

    with pytest.raises(IntegrityError) as raised:
        async with engine.connect() as connection:
            async with connection.begin():
                await connection.execute(
                    text("INSERT INTO person (org_id) VALUES (:org)"),
                    {"org": str(phantom_org)},
                )

    assert "fk_person_org_id" in str(raised.value)


async def test_person_email_insert_cross_org_parent_rejected(migrated_db: None) -> None:
    """A child row whose org_id differs from its parent's must die on the composite FK —
    the cross-tenant negative test for the DB layer (M-3/L-10 teeth).

    Everything runs in ONE transaction the violation aborts, so nothing persists.
    """
    org_a, org_b, person_id = uuid4(), uuid4(), uuid4()

    with pytest.raises(IntegrityError) as raised:
        async with engine.connect() as connection:
            async with connection.begin():
                for org_id, slug in ((org_a, "dq-selftest-a"), (org_b, "dq-selftest-b")):
                    await connection.execute(
                        text(
                            "INSERT INTO organizations (id, name, slug) "
                            "VALUES (:id, 'DQ Selftest', :slug)"
                        ),
                        {"id": str(org_id), "slug": slug},
                    )
                await connection.execute(
                    text("INSERT INTO person (id, org_id) VALUES (:id, :org)"),
                    {"id": str(person_id), "org": str(org_a)},
                )
                await connection.execute(
                    text(
                        "INSERT INTO person_email (org_id, person_id, email) "
                        "VALUES (:org, :person, 'dq-selftest@example.com')"
                    ),
                    {"org": str(org_b), "person": str(person_id)},
                )

    assert "fk_person_email_person_id_org" in str(raised.value), (
        "cross-org child insert must fail on the COMPOSITE FK, not some other constraint: "
        f"{raised.value}"
    )


async def test_support_grant_insert_revoked_from_requested_allowed(migrated_db: None) -> None:
    """A revoked grant with decided_* all NULL (revoke never stamps a decision) is LEGAL —
    the deliberate divergence from the audit sketch must not reject the real lifecycle."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        result = await connection.execute(
            text(
                "INSERT INTO support_grant (org_id, requested_by_admin_id, reason, status) "
                "VALUES (:org, :admin, 'dq lifecycle selftest', 'revoked') RETURNING id"
            ),
            {"org": str(uuid4()), "admin": str(uuid4())},
        )
        inserted_id = result.scalar_one()
        await transaction.rollback()  # never persist the probe row

    assert inserted_id is not None


async def test_support_grant_insert_approved_without_expiry_rejected(migrated_db: None) -> None:
    """An approved grant missing its time box / decision stamps must die on the lifecycle CHECK."""
    with pytest.raises(IntegrityError) as raised:
        async with engine.connect() as connection:
            async with connection.begin():
                await connection.execute(
                    text(
                        "INSERT INTO support_grant (org_id, requested_by_admin_id, reason, "
                        "status, decided_at, decided_by_user_id) "
                        "VALUES (:org, :admin, 'dq lifecycle selftest', 'approved', now(), :user)"
                    ),
                    {"org": str(uuid4()), "admin": str(uuid4()), "user": str(uuid4())},
                )

    assert "ck_support_grant_lifecycle" in str(raised.value)
