"""define row-level security policies (defined, enforcement deferred)

DEFINES the tenant-isolation RLS policy for the `users` table — security.md layer 2:
"RLS policies defined (enforced in production, defined in prototype)". The policy keys
on the `app.current_org_id` GUC that core.scoped_session binds per transaction.

ENFORCEMENT IS CURRENTLY INERT, BY DESIGN: the application connects as the bootstrap
Postgres role `oneai`, which is a SUPERUSER and the table OWNER — both bypass RLS
unconditionally (verified: pg_roles.rolsuper = rolbypassrls = true). The policy
therefore does nothing until the app connects as a dedicated non-superuser, non-owner
role with a bypass path for the legitimately-global login lookup (tracked in
docs/FIX_BEFORE_PROD.md). We DEFINE it now so the rule is written down and ready; the
active control today remains the application-layer org_id filter in the repositories.

Only `users` carries org_id (TenantMixin). `organizations` is the tenant root and
`refresh_tokens` is subject-keyed (no org_id), so neither receives a policy here.

Revision ID: 0003_define_rls_policies
Revises: 0002_identity_tables
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_define_rls_policies"
down_revision: str | None = "0002_identity_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # current_setting('app.current_org_id', true) returns NULL when the GUC is unset,
    # so an unscoped session matches no rows (fail-closed) once enforcement is active.
    # No FORCE ROW LEVEL SECURITY: that switch, plus connecting as a non-superuser
    # role, is the deferred "enforce in production" step (see docs/FIX_BEFORE_PROD.md).
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY org_isolation ON users
            USING (org_id = current_setting('app.current_org_id', true)::uuid)
            WITH CHECK (org_id = current_setting('app.current_org_id', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS org_isolation ON users")
    op.execute("ALTER TABLE users DISABLE ROW LEVEL SECURITY")
