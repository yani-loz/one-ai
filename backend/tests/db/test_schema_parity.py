"""
Role: Standing model↔migration drift guard — Alembic autogenerate must produce NO diff between the
      ORM models and the migrated schema. Converts the one-time parity verification into a permanent
      gate: any future model change without a matching migration (or vice-versa) fails here.
Used by: pytest, against a MIGRATED DB (CI runs `alembic upgrade head` before pytest).
Depends on: alembic.autogenerate, app.common.base_model (Base), app.core.database, and EVERY
            domain's model package (so Base.metadata is complete — a missing import would otherwise
            surface as a spurious 'remove table' diff).
Key invariants:
  - On a migrated DB, compare_metadata(Base.metadata) returns no diff (ignoring alembic_version).
  - Skips loudly on a non-migrated DB (no alembic_version), so a fresh create_all run doesn't fail.
"""

from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

import app.connectors.imap.models  # noqa: F401 — register email Layer-1 tables
import app.connectors.models  # noqa: F401 — register connector tables
import app.entities.models  # noqa: F401 — register entity-graph tables
import app.identity.models  # noqa: F401 — register identity tables
from app.common.base_model import Base
from app.core.database import engine


async def test_models_match_migrated_schema() -> None:
    """Fail if the ORM models and the Alembic-migrated schema have drifted apart."""

    def _diff(sync_connection: object) -> list[object] | None:
        if "alembic_version" not in set(inspect(sync_connection).get_table_names()):
            return None  # not a migrated DB → caller skips
        context = MigrationContext.configure(sync_connection)
        return compare_metadata(context, Base.metadata)

    async with engine.connect() as connection:
        diffs = await connection.run_sync(_diff)

    if diffs is None:
        pytest.skip("DB not migrated (no alembic_version) — run `alembic upgrade head` first.")

    drift = [entry for entry in diffs if "alembic_version" not in repr(entry)]
    assert not drift, f"model/migration drift detected: {drift}"
