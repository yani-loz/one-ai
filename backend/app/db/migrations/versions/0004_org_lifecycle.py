"""organization lifecycle: status CHECK + legal_hold

Pins organizations.status to the OrganizationStatus enum
(active|suspended|onboarding|offboarded) with a named CHECK, and adds the
legal_hold boolean column (default false). Mirrors app.identity.models.organization
exactly so autogenerate sees no drift.

The CHECK is named ck_organizations_status to match the model's __table_args__.
Existing rows are all 'active' (the prior server default), so the CHECK validates
cleanly on add. legal_hold has a server default so the NOT NULL backfills existing rows.

Revision ID: 0004_org_lifecycle
Revises: 0003_define_rls_policies
Create Date: 2026-06-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_org_lifecycle"
down_revision: str | None = "0003_define_rls_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "legal_hold",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_organizations_status",
        "organizations",
        "status IN ('active', 'suspended', 'onboarding', 'offboarded')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_organizations_status", "organizations", type_="check")
    op.drop_column("organizations", "legal_hold")
