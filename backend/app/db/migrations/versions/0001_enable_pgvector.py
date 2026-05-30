"""enable pgvector extension

The semantic-memory layer (Bible §6/§15) stores embeddings in pgvector. Enabling
the extension is the first migration so every later table can declare vector
columns. Requires a superuser role (true in the dev pgvector image).

Revision ID: 0001_enable_pgvector
Revises:
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_enable_pgvector"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
