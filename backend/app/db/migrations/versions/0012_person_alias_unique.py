"""add UNIQUE(org_id, person_id, alias) on person_alias — dedup name aliases

The entity resolver now records a `person_alias` for each distinct display-name a person is seen
under (audit DQ-K04: 38% of people were nameless because the name was only ever set once at creation
and never enriched). The resolver writes an alias on every sighting; this UNIQUE makes that write
idempotent the same race-safe way the rest of get-or-create works — INSERT, and on the constraint
violation roll back the SAVEPOINT and move on — so a name seen N times yields ONE alias row, not N.
The table is empty today (no aliases were ever written), so the constraint adds cleanly.

Revision ID: 0012_person_alias_unique
Revises: 0011_sync_runner_state
Create Date: 2026-06-09
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012_person_alias_unique"
down_revision: str | None = "0011_sync_runner_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_person_alias_identity", "person_alias", ["org_id", "person_id", "alias"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_person_alias_identity", "person_alias", type_="unique")
