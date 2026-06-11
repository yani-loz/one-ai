"""add connector_connection.disabled_at — reversible admin disable

Adds the persistent disable state (design §8 connector lifecycle) as a nullable timestamp,
orthogonal to `status` (verification health): NULL = active; set = disabled (a company_admin
turned the connector off — sync stops now, and AI retrieval is excluded once that path enforces
it). Reversible: enable clears it. A timestamp (not a status enum value) keeps the health state
intact across a disable/enable cycle and records WHEN it was disabled.

Revision ID: 0010_connector_disabled_at
Revises: 0009_enforce_rls
Create Date: 2026-06-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_connector_disabled_at"
down_revision: str | None = "0009_enforce_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "connector_connection",
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connector_connection", "disabled_at")
