"""
Standing-invariant + live-enforcement tests for the attachment-extraction columns (migration 0015).

Two layers of guarantee, mirroring tests/db/test_data_quality_guards.py:

  1. STANDING INVARIANT (catalog) — proves via information_schema/pg_catalog that 0015 landed
     exactly: extraction_status NOT NULL with DEFAULT 'pending', nullable extractor_name/
     extractor_version, the CHECK pinning the closed status vocabulary, and the
     (org_id, content_hash) backfill-lookup index. The CHECK is additionally asserted to match
     app.connectors.imap.parsing.extraction_result.EXTRACTION_STATUSES — the Python source of
     truth — so the DB vocabulary and the seam's constants can NEVER silently drift.

  2. LIVE ENFORCEMENT — proves the guard bites: an attachment row stamped with a status outside
     the vocabulary dies on the CHECK; a row inserted without a status lands as 'pending'.

Requires a migrated DB (alembic upgrade head, ≥0015); skips loudly otherwise. Live tests run on
the OWNER engine (schema enforcement is role-independent) inside transactions that are rolled
back or aborted by the expected violation — nothing persists.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.connectors.imap.parsing.extraction_result import EXTRACTION_STATUSES
from app.core.database import engine

_STATUS_CHECK_NAME = "ck_email_attachment_extraction_status"
_HASH_INDEX_NAME = "ix_email_attachment_org_content_hash"


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
    """Skip unless the DB is migrated through 0015 (the columns are migration-only DDL)."""
    async with engine.connect() as connection:
        migrated = (
            await connection.execute(text("SELECT to_regclass('public.alembic_version')"))
        ).scalar() is not None
        column_present = (
            await connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                    "AND table_name = 'email_attachment' "
                    "AND column_name = 'extraction_status'"
                )
            )
        ).scalar() is not None
    if not migrated or not column_present:
        pytest.skip("DB not migrated through 0015 — run `alembic upgrade head` first.")
    yield


# — 1) Standing invariants (catalog, owner engine) —


async def test_extraction_columns_exist_with_expected_shapes(migrated_db: None) -> None:
    """extraction_status is NOT NULL DEFAULT 'pending'; the provenance pair is nullable text."""
    async with engine.connect() as connection:
        columns = {
            row[0]: (row[1], row[2], row[3])
            for row in await connection.execute(
                text(
                    "SELECT column_name, data_type, is_nullable, column_default "
                    "FROM information_schema.columns WHERE table_schema = 'public' "
                    "AND table_name = 'email_attachment' AND column_name IN "
                    "('extraction_status', 'extractor_name', 'extractor_version')"
                )
            )
        }

    assert set(columns) == {"extraction_status", "extractor_name", "extractor_version"}, (
        f"0015 columns missing: {columns}"
    )
    status_type, status_nullable, status_default = columns["extraction_status"]
    assert (status_type, status_nullable) == ("text", "NO")
    assert status_default is not None and "pending" in status_default
    assert columns["extractor_name"] == ("text", "YES", None)
    assert columns["extractor_version"] == ("text", "YES", None)


async def test_extraction_status_check_matches_python_vocabulary(migrated_db: None) -> None:
    """The DB CHECK and parsing.extraction_result.EXTRACTION_STATUSES are the SAME set —
    the lockstep guarantee the 0015 docstring promises (drift here breaks honest-NULL)."""
    async with engine.connect() as connection:
        definition = await _constraint_definition(
            connection, "email_attachment", _STATUS_CHECK_NAME
        )

    assert definition is not None, f"{_STATUS_CHECK_NAME} missing"
    db_statuses = set(re.findall(r"'([a-z_]+)'", definition))
    assert db_statuses == EXTRACTION_STATUSES, (
        "extraction-status vocabulary drift between migration 0015 and "
        f"extraction_result.EXTRACTION_STATUSES: DB-only={db_statuses - EXTRACTION_STATUSES} "
        f"Python-only={EXTRACTION_STATUSES - db_statuses}"
    )


async def test_content_hash_lookup_index_exists(migrated_db: None) -> None:
    """The (org_id, content_hash) backfill/dedup lookup index (deferred by 0014 to CA-CONN-04)."""
    async with engine.connect() as connection:
        definition = (
            await connection.execute(
                text(
                    "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' "
                    "AND indexname = :name"
                ),
                {"name": _HASH_INDEX_NAME},
            )
        ).scalar_one_or_none()

    assert definition is not None, f"{_HASH_INDEX_NAME} missing"
    assert "(org_id, content_hash)" in definition, f"wrong index shape: {definition}"


# — 2) Live enforcement (owner engine; transactions never commit) —


async def _insert_attachment_parents(connection: AsyncConnection) -> tuple[str, str]:
    """Seed org → connector_connection → email_message inside the CALLER's transaction;
    returns the (org_id, email_id) pair the attachment under test must reference."""
    org_id, connection_id, email_id = uuid4(), uuid4(), uuid4()
    await connection.execute(
        text("INSERT INTO organizations (id, name, slug) VALUES (:id, 'Ext Selftest', :slug)"),
        {"id": str(org_id), "slug": f"ext-selftest-{org_id.hex[:8]}"},
    )
    await connection.execute(
        text(
            "INSERT INTO connector_connection (id, org_id, connector_type, display_name, "
            "auth_method, username, config, secret_ciphertext, secret_key_version) "
            "VALUES (:id, :org, 'imap', 'Ext Selftest', 'app_password', 'x@y.z', "
            "'{}'::jsonb, '\\x00'::bytea, 1)"
        ),
        {"id": str(connection_id), "org": str(org_id)},
    )
    await connection.execute(
        text(
            "INSERT INTO email_message (id, org_id, connection_id, dedup_key) "
            "VALUES (:id, :org, :conn, :key)"
        ),
        {
            "id": str(email_id),
            "org": str(org_id),
            "conn": str(connection_id),
            "key": f"sha256:{uuid4().hex}",
        },
    )
    return str(org_id), str(email_id)


async def test_attachment_insert_bogus_status_rejected(migrated_db: None) -> None:
    """A status outside the pinned vocabulary must die on the CHECK — the teeth of 0015."""
    with pytest.raises(IntegrityError) as raised:
        async with engine.connect() as connection:
            async with connection.begin():
                org_id, email_id = await _insert_attachment_parents(connection)
                await connection.execute(
                    text(
                        "INSERT INTO email_attachment (org_id, email_id, extraction_status) "
                        "VALUES (:org, :email, 'definitely_not_a_status')"
                    ),
                    {"org": org_id, "email": email_id},
                )

    assert _STATUS_CHECK_NAME in str(raised.value)


async def test_attachment_insert_without_status_defaults_to_pending(migrated_db: None) -> None:
    """A row inserted without a status lands as 'pending' — the backfill's work-queue marker."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        org_id, email_id = await _insert_attachment_parents(connection)
        status = (
            await connection.execute(
                text(
                    "INSERT INTO email_attachment (org_id, email_id) "
                    "VALUES (:org, :email) RETURNING extraction_status"
                ),
                {"org": org_id, "email": email_id},
            )
        ).scalar_one()
        await transaction.rollback()  # never persist the probe rows

    assert status == "pending"
