"""enforce row-level security — role split, grants, FORCE RLS

FLIPS tenant Row-Level Security from DEFINED (inert) to ENFORCED. The org_isolation policies on the
12 tenant tables (migrations 0003/0006/0007/0008) are already correct and fail-closed (NULL GUC ->
zero rows); they did nothing only because the app connected as the SUPERUSER+OWNER role `oneai`,
which bypasses RLS. This migration creates the two least-privilege runtime roles and FORCEs RLS so
tenant isolation finally applies at the DATABASE layer — the keystone of the sovereignty promise.

Design (docs/rls-jwt-enforcement-plan.md): three roles, two runtime engines.
  - oneai        — SUPERUSER+OWNER, unchanged. Alembic/DDL only (runs THIS migration); never HTTP.
  - oneai_app    — NOSUPERUSER, non-owner, NO BYPASSRLS. The tenant engine; RLS enforces against it.
  - oneai_global — NOSUPERUSER, non-owner, BYPASSRLS. The global engine; cross-org / pre-org flows.

Roles are created NOLOGIN here (no password in VCS). scripts.provision_roles sets each role's
LOGIN + password out-of-band from config (the single source). Role names MUST match
config.app_db_user / config.global_db_user.

FORCE ROW LEVEL SECURITY is belt-and-suspenders: the runtime roles are non-owners, so the ENABLE'd
policies already apply to them — FORCE additionally subjects a non-superuser table OWNER to RLS, so
no future owner-connection can silently bypass. (The superuser `oneai` still bypasses
unconditionally — that is the DDL/provisioning role only, never serving a request.)

Revision ID: 0009_enforce_rls
Revises: 0008_email_ingestion_schema
Create Date: 2026-06-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009_enforce_rls"
down_revision: str | None = "0008_email_ingestion_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every tenant (org-scoped) table — each already carries ENABLE RLS + an org_isolation policy from
# its own migration. This migration adds FORCE on each. Keep in sync with the dynamically-enumerated
# standing-invariant test (tests/identity/models/test_rls_invariants.py): a new tenant table that
# forgets FORCE fails there.
_TENANT_TABLES = [
    "users",  # 0003
    "support_grant",  # 0006
    "connector_connection",  # 0007
    "person",  # 0008
    "person_email",
    "person_alias",
    "company",
    "company_domain",
    "person_company",
    "email_message",
    "email_recipient",
    "email_attachment",
]

# Runtime role names — MUST match config.app_db_user / config.global_db_user.
_APP_ROLE = "oneai_app"
_GLOBAL_ROLE = "oneai_global"


def _create_role(role: str, *, bypassrls: bool) -> None:
    """CREATE a NOLOGIN runtime role idempotently (roles are cluster-global; survive DB resets)."""
    bypass = "BYPASSRLS" if bypassrls else "NOBYPASSRLS"
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                CREATE ROLE {role} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE {bypass};
            END IF;
        END
        $$
        """
    )


def upgrade() -> None:
    # 1) Runtime roles (NOLOGIN; LOGIN+password set out-of-band by scripts.provision_roles).
    _create_role(_APP_ROLE, bypassrls=False)
    _create_role(_GLOBAL_ROLE, bypassrls=True)

    # 2) DML privileges on existing + future tables/sequences. GRANT is table-level privilege; RLS
    #    is row-level — both are required (RLS still filters rows for the non-bypass oneai_app).
    for role in (_APP_ROLE, _GLOBAL_ROLE):
        op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")
        op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}")
        # Future tables/seqs created by the owner (later migrations, test create_all) auto-grant.
        op.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}"
        )
        op.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public "
            f"GRANT USAGE, SELECT ON SEQUENCES TO {role}"
        )

    # 3) FORCE RLS on every tenant table (belt-and-suspenders; the runtime roles are non-owners).
    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")

    for role in (_APP_ROLE, _GLOBAL_ROLE):
        op.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public "
            f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {role}"
        )
        op.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public "
            f"REVOKE USAGE, SELECT ON SEQUENCES FROM {role}"
        )
        # DROP OWNED BY revokes every grant TO the role (and drops anything it owns), so the
        # following DROP ROLE won't error on dependent privileges. DROP ROLE still fails if a
        # connection is live as the role — drain the runtime engines first (see the plan).
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    EXECUTE 'DROP OWNED BY {role}';
                    EXECUTE 'DROP ROLE {role}';
                END IF;
            END
            $$
            """
        )
