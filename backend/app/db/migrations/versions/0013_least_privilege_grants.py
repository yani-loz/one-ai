"""least-privilege grants — strip the tenant role off the platform plane + audit_log RLS

Role: Narrows migration 0009's blanket `GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES` for the
      tenant role `oneai_app` to exactly what tenant-engine code paths use. Threat model: a tenant-
      engine compromise (SQLi or a mis-scoped query on the RLS-bound pool) must NOT reach platform
      credentials or cross-org compliance data — platform_admins password hashes, forge-able
      refresh_tokens, the organizations registry, the alembic_version ledger, or other orgs'
      audit_log rows (actor_email / ip_address).
Used by: alembic upgrade head (runs as the OWNER role `oneai`, which both created the 0009 grants
         and owns the tables — the only role that can revoke them).
Depends on: 0012_person_alias_unique (revision chain); the roles + grants + default ACLs created by
            0009_enforce_rls; the audit_log table + append-only trigger from 0005_audit_log.
Key invariants:
  - oneai_app keeps full DML ONLY on the 14 FORCE-RLS tenant tables (12 from 0009 + 2 from 0011)
    and INSERT + SELECT on audit_log. It holds ZERO privileges on the platform plane:
    platform_admins, refresh_tokens, organizations, alembic_version. Verified code paths: those
    tables' repositories (PlatformAdminRepository / RefreshTokenRepository / OrganizationRepository)
    are constructed only inside providers on Depends(get_session) — the GLOBAL engine — in
    app/identity/dependencies.py (auth/login/refresh, platform admin, erasure); alembic_version is
    read only on the OWNER engine (alembic CLI, tests/db). No tenant-engine reader exists, so no
    minimal-verb carve-out is granted.
  - audit_log is append-only for the tenant role at the PRIVILEGE layer too: UPDATE/DELETE revoked
    (the 0005 trigger stays the enforcement for everyone else). SELECT is kept — SQLAlchemy's
    INSERT .. RETURNING (server-default id/occurred_at) requires it — but is now POLICY-scoped:
    ENABLE + FORCE RLS with the standard org_isolation policy (0011 idiom), so a tenant-engine read
    that forgets an org filter sees only the GUC org's rows, and a tenant write must stamp
    org_id = GUC. Verified writers: the only tenant-engine audit writers are UserService
    (org_id=target.org_id) and CompanySupportService (org_id=grant.org_id) — both org ids come off
    RLS-scoped rows so they always equal the GUC; every org_id-NULL platform event (login failure/
    logout/platform actions) is written on the GLOBAL engine (get_session or
    AuditService.record_independently's GlobalSessionLocal), which BYPASSRLS leaves untouched.
  - oneai_global is deliberately UNCHANGED (BYPASSRLS platform plane needs cross-org reach), and it
    KEEPS UPDATE/DELETE on audit_log: append-only for it is enforced by the 0005 trigger, and
    tests/identity/models/test_audit_log.py asserts that trigger's error surface — a privilege
    revoke would merely shadow the trigger with "permission denied".
  - DEFAULT PRIVILEGES: 0009's auto-grant of full DML on FUTURE tables to oneai_app is REVOKED —
    a new table no longer becomes tenant-readable by default (fail-closed). NEW CONVENTION: a
    migration creating a tenant table must now GRANT SELECT, INSERT, UPDATE, DELETE on it to
    oneai_app explicitly (oneai_global still auto-grants). The standing test
    tests/db/test_least_privilege_grants.py enumerates TenantMixin tables and fails on a forgotten
    grant. Sequence default ACLs are untouched (no sequences exist; UUID PKs throughout).
  - downgrade() restores the exact 0009 state: blanket re-grant on the four platform tables,
    UPDATE/DELETE back on audit_log, audit_log RLS/policy dropped, default ACL re-granted.

Revision ID: 0013_least_privilege_grants
Revises: 0012_person_alias_unique
Create Date: 2026-06-10
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013_least_privilege_grants"
down_revision: str | None = "0012_person_alias_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Runtime tenant role — MUST match config.app_db_user (0009 created it).
_APP_ROLE = "oneai_app"

# The platform plane: tables NO tenant-engine code path touches (verified — see module docstring).
# oneai_app loses every privilege on each.
_PLATFORM_ONLY_TABLES = [
    "platform_admins",  # bcrypt hashes — global engine only (PlatformAdminRepository)
    "refresh_tokens",  # session tokens — global engine only (RefreshTokenRepository)
    "organizations",  # org registry/status — global engine only (OrganizationRepository)
    "alembic_version",  # migration ledger — owner engine only (alembic CLI)
]

# The GUC the org_isolation policies key on (0003/0011 idiom; core.scoped_session binds it).
_ORG_GUC = "current_setting('app.current_org_id', true)::uuid"


def upgrade() -> None:
    # 1) Platform plane: the tenant role holds nothing here. REVOKE ALL covers the 0009 blanket
    #    SELECT/INSERT/UPDATE/DELETE (table-level revoke; oneai_global keeps its grants).
    for table in _PLATFORM_ONLY_TABLES:
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM {_APP_ROLE}")

    # 2) audit_log: append-only at the privilege layer for the tenant role. INSERT stays (tenant
    #    flows write user.*/support.* events); SELECT stays for INSERT .. RETURNING but is
    #    policy-scoped below. The 0005 trigger remains the append-only enforcement for oneai_global.
    op.execute(f"REVOKE UPDATE, DELETE ON TABLE audit_log FROM {_APP_ROLE}")

    # 3) audit_log RLS: ENABLE + org_isolation + FORCE (0011 idiom). For the non-bypass tenant role
    #    this scopes SELECT to the GUC org (an org-filter-less tenant read can no longer leak other
    #    orgs' actor_email/ip_address) and forces writes to stamp org_id = GUC. org_id-NULL platform
    #    events are written on the BYPASSRLS global engine, which this does not touch.
    op.execute("ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY org_isolation ON audit_log
            USING (org_id = {_ORG_GUC})
            WITH CHECK (org_id = {_ORG_GUC})
        """
    )
    op.execute("ALTER TABLE audit_log FORCE ROW LEVEL SECURITY")

    # 4) Future tables stop auto-granting to the tenant role (0009 set this FOR ROLE CURRENT_USER —
    #    the owner `oneai`, which also runs this migration, so the entry matches). From here on, a
    #    migration creating a tenant table grants oneai_app explicitly; the standing grants test
    #    fails on a forgotten grant. oneai_global's default ACLs are kept.
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {_APP_ROLE}"
    )


def downgrade() -> None:
    # Exact 0009 restoration, in reverse order of upgrade().
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {_APP_ROLE}"
    )

    op.execute("ALTER TABLE audit_log NO FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS org_isolation ON audit_log")
    op.execute("ALTER TABLE audit_log DISABLE ROW LEVEL SECURITY")

    op.execute(f"GRANT UPDATE, DELETE ON TABLE audit_log TO {_APP_ROLE}")

    for table in _PLATFORM_ONLY_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO {_APP_ROLE}")
