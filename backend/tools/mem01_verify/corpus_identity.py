"""
Role: CORPUS_DIGEST_V1 (contract §5.2) — the candidate's database identity. Reads every
      gate-relevant column of every gate-relevant table for one org inside the caller's R6
      snapshot, renders each row as one canonical JSON line prefixed by its table name, and
      digests the whole roster; reports the §5.1 `text_digest` and the per-table roster counts
      beside it.
Used by: tools.mem01_verify.run_identity (the closure's `corpus_digest`), .census (`Census`
      carries the digest), .release (the manifest's `corpus` block), .verify_step1 (step 4), and
      the sealed oracle module tests/tools/mem01_verify/test_corpus_identity.py.
Depends on: tools.mem01_verify.hashing (`canonical_lines_digest`, `sha256_bytes`), .snapshot
      (`snapshot_record`, `text_digest_line` — so the text digest is the emitter's rule, not a
      second copy of it), .db (`read_alembic_version`, §16.15 — imported inside
      `_alembic_lines` so `db` stays importable without this module); sqlalchemy;
      app.core.config only as the fallback for the server endpoint.
Key invariants:
  - Personal-data columns enter ONLY as the sha256 of their UTF-8 bytes (`*_sha256`); no address,
    name, subject, body, filename or alias text ever reaches the digest input or a report (R5).
  - Every query is explicitly filtered by `org_id`, so the digest is tenant-specific on any
    session plane, and read-only (R6) — the caller owns the snapshot transaction.
  - `text_digest` is a strict function of the §5.1 text artifacts (`email_body` and
    `email_subject` for every email, `attachment_text` for every non-NULL extracted text) and
    therefore equals what `snapshot.emit_snapshot` reports for the same snapshot.
  - Any change to any listed value, to its nullness, or to the row set moves `corpus_digest`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import text

from tools.mem01_verify.hashing import canonical_lines_digest, sha256_bytes
from tools.mem01_verify.snapshot import snapshot_record, text_digest_line

if TYPE_CHECKING:  # annotations only — nothing here needs the class at runtime
    from sqlalchemy.ext.asyncio import AsyncSession

CORPUS_DIGEST_VERSION = "CORPUS_DIGEST_V1"

PLAIN = "plain"
HASH = "hash"
HASH_TEXT = "hash_text"
IS_NULL = "is_null"
JSON_HASH = "json_hash"

SpecColumn = tuple[str, str, str]

TIMESTAMP_COLUMNS: tuple[str, ...] = ("created_at", "updated_at")
"""The `TimestampMixin` columns — part of "all columns" for the three §5.2 all-column tables."""


@dataclass(frozen=True)
class CorpusIdentity:
    """The identity of one org's measured database state at one snapshot (§1.4)."""

    version: str
    corpus_digest: str
    text_digest: str
    roster_counts: Mapping[str, int]
    taken_at: datetime
    snapshot_transaction_id: str
    database: str
    host: str
    port: int
    org_id: UUID


def _plain(*names: str) -> tuple[SpecColumn, ...]:
    """Columns copied as stored (UUIDs and timestamps rendered canonically)."""
    return tuple((name, name, PLAIN) for name in names)


def _hashed(*names: str) -> tuple[SpecColumn, ...]:
    """Personal-data columns that enter as `<name>_sha256`; a NULL stays null."""
    return tuple((f"{name}_sha256", name, HASH) for name in names)


def _stored_text(column: str, prefix: str) -> tuple[SpecColumn, ...]:
    """A stored-text column: its sha256 (NULL hashed as the empty string) plus its nullness."""
    return ((f"{prefix}_sha256", column, HASH_TEXT), (f"{prefix}_stored_null", column, IS_NULL))


@dataclass(frozen=True)
class _TableSpec:
    """One table of §5.2: the columns to read and how each enters the digest line."""

    table: str
    columns: tuple[SpecColumn, ...]


_EMAIL_MESSAGE = _TableSpec(
    "email_message",
    (
        *_plain(
            "id",
            "connection_id",
            "dedup_key",
            "message_id",
            "in_reply_to",
            "references",
            "from_person_id",
            "sent_at",
            "received_at",
            "direction",
            "is_automated",
            "is_reply",
            "has_attachments",
            "word_count",
            "language",
            "headers",
            "size_bytes",
            "parse_status",
            "visibility_scope",
            "origin_scope",
            "container_id",
        ),
        *_hashed("from_address", "subject"),
        *_stored_text("body_text", "body"),
    ),
)

_EMAIL_ATTACHMENT = _TableSpec(
    "email_attachment",
    (
        *_plain(
            "id",
            "email_id",
            "content_type",
            "size_bytes",
            "content_hash",
            "is_inline",
            "content_id",
            "extraction_status",
            "extractor_name",
            "extractor_version",
            "extraction_detail",
            "visibility_scope",
            "origin_scope",
            "container_id",
        ),
        *_hashed("filename"),
        *_stored_text("extracted_text", "extracted_text"),
        ("extracted_data_sha256", "extracted_data", JSON_HASH),
    ),
)

_TABLE_SPECS: tuple[_TableSpec, ...] = (
    _EMAIL_MESSAGE,
    _EMAIL_ATTACHMENT,
    _TableSpec(
        "email_recipient",
        (*_plain("id", "email_id", "kind", "person_id"), *_hashed("address")),
    ),
    _TableSpec("person", (*_plain("id", "is_internal"), *_hashed("display_name"))),
    _TableSpec("person_email", (*_plain("id", "person_id", "source"), *_hashed("email"))),
    _TableSpec("person_alias", (*_plain("id", "person_id", "source"), *_hashed("alias"))),
    _TableSpec(
        "acl_grant",
        _plain(
            "id",
            "person_id",
            "object_type",
            "object_id",
            "connection_id",
            "provenance",
            "granted_at",
            "revoked_at",
        ),
    ),
    _TableSpec(
        "principal_source_identity",
        (
            *_plain(
                "id",
                "person_id",
                "source_type",
                "verified",
                "verified_at",
                "verified_by_user_id",
                *TIMESTAMP_COLUMNS,
            ),
            *_hashed("external_id"),
        ),
    ),
    _TableSpec(
        "visibility_promotion",
        _plain(
            "id",
            "object_type",
            "object_id",
            "from_scope",
            "to_scope",
            "approved_by_user_id",
            "audit_log_id",
            *TIMESTAMP_COLUMNS,
        ),
    ),
    _TableSpec(
        "fact_provenance",
        _plain(
            "id",
            "fact_id",
            "source_object_type",
            "source_object_id",
            "status",
            *TIMESTAMP_COLUMNS,
        ),
    ),
    _TableSpec("connector_connection", (*_plain("id"), *_hashed("username"))),
)


def _canonical_json(payload: object) -> str:
    """Canonical JSON text: sorted keys, no incidental whitespace, non-ASCII kept raw (§5.2)."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _encode(value: object, mode: str) -> object:
    """Render one column value for the digest line according to its §5.2 mode."""
    if mode == IS_NULL:
        return value is None
    if mode == HASH_TEXT:
        return sha256_bytes(("" if value is None else str(value)).encode("utf-8"))
    if value is None:
        return None
    if mode == HASH:
        return sha256_bytes(str(value).encode("utf-8"))
    if mode == JSON_HASH:
        return sha256_bytes(_canonical_json(value).encode("utf-8"))
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value


def _select_sql(spec: _TableSpec, columns: Sequence[str]) -> str:
    """The read-only SELECT for one table: explicit columns, org-filtered, ordered by id."""
    projected = ", ".join(f'"{name}"' for name in columns)
    return (
        f"SELECT {projected} FROM {spec.table} "
        "WHERE org_id = CAST(:org AS uuid) ORDER BY id"  # noqa: S608 - names are module constants
    )


async def _table_lines(conn: AsyncSession, org_id: UUID, spec: _TableSpec) -> list[str]:
    """Return one canonical `"<table>\\t<json>"` line per row of `spec` for `org_id`."""
    columns = tuple(dict.fromkeys(column for _, column, _ in spec.columns))
    position = {name: index for index, name in enumerate(columns)}
    result = await conn.execute(text(_select_sql(spec, columns)), {"org": str(org_id)})
    lines: list[str] = []
    for row in result.all():
        payload = {key: _encode(row[position[column]], mode) for key, column, mode in spec.columns}
        lines.append(f"{spec.table}\t{_canonical_json(payload)}")
    return lines


async def _alembic_lines(conn: AsyncSession) -> list[str]:
    """The Alembic head row — the schema half of the corpus identity (§5.2).

    Migration 0013 revoked `alembic_version` from the write role the R6 snapshot runs as, so the
    revision is read through the sanctioned global-plane helper of §16.15, keyed by the
    snapshot's own `current_database()`.
    """
    from tools.mem01_verify.db import read_alembic_version  # local: db imports nothing from here

    database = (await conn.execute(text("SELECT current_database()"))).scalar_one()
    version = await read_alembic_version(str(database))
    return [f"alembic_version\t{_canonical_json({'version_num': version})}"]


async def _text_digest(conn: AsyncSession, org_id: UUID) -> str:
    """Recompute the §5.1 `text_digest` for this org from the same snapshot."""
    emails = (
        await conn.execute(
            text(
                "SELECT id, subject, body_text, parse_status FROM email_message "
                "WHERE org_id = CAST(:org AS uuid) ORDER BY id"
            ),
            {"org": str(org_id)},
        )
    ).all()
    attachments = (
        await conn.execute(
            text(
                "SELECT id, extracted_text, extractor_name, extractor_version, extraction_status "
                "FROM email_attachment WHERE org_id = CAST(:org AS uuid) "
                "AND extracted_text IS NOT NULL ORDER BY id"
            ),
            {"org": str(org_id)},
        )
    ).all()
    records = []
    for row in emails:
        versions = {"parse_status": row[3]}
        records.append(
            snapshot_record("email_body", "email_message", row[0], org_id, row[2], versions)
        )
        records.append(
            snapshot_record("email_subject", "email_message", row[0], org_id, row[1], versions)
        )
    for row in attachments:
        records.append(
            snapshot_record(
                "attachment_text",
                "email_attachment",
                row[0],
                org_id,
                row[1],
                {
                    "extractor_name": row[2],
                    "extractor_version": row[3],
                    "extraction_status": row[4],
                },
            )
        )
    return canonical_lines_digest(
        text_digest_line(record.artifact_id, record.sha256, record.stored_null)
        for record in records
    )


def _server_endpoint(conn: AsyncSession) -> tuple[str, int]:
    """Where this connection points: the bound engine's URL, or the configured settings."""
    bind = getattr(conn, "bind", None) or getattr(conn, "engine", None)
    url = getattr(bind, "url", None)
    host = getattr(url, "host", None)
    if isinstance(host, str) and host:
        return host, int(getattr(url, "port", None) or 5432)
    from app.core.config import get_settings

    settings = get_settings()
    return settings.postgres_host, int(settings.postgres_port)


async def corpus_digest(conn: AsyncSession, org_id: UUID) -> CorpusIdentity:
    """Compute `CORPUS_DIGEST_V1` for one org inside the caller's read-only snapshot (§5.2).

    Every gate-relevant column of every gate-relevant table is read for `org_id`, rendered as a
    canonical JSON line prefixed by its table name (personal-data columns as sha256), and
    digested with `canonical_lines_digest`; the §5.1 `text_digest` and the per-table roster
    counts are reported beside it.

    Args:
        conn: an open async session whose transaction is the R6 snapshot (`REPEATABLE READ`,
            `READ ONLY`) — every read here belongs to that one snapshot.
        org_id: the tenant whose corpus is identified.

    Returns:
        The frozen `CorpusIdentity`.
    """
    lines: list[str] = []
    roster_counts: dict[str, int] = {}
    for spec in _TABLE_SPECS:
        table_lines = await _table_lines(conn, org_id, spec)
        roster_counts[spec.table] = len(table_lines)
        lines.extend(table_lines)
    lines.extend(await _alembic_lines(conn))
    transaction_id = (
        await conn.execute(text("SELECT pg_current_snapshot()::text, current_database()"))
    ).one()
    host, port = _server_endpoint(conn)
    return CorpusIdentity(
        version=CORPUS_DIGEST_VERSION,
        corpus_digest=canonical_lines_digest(lines),
        text_digest=await _text_digest(conn, org_id),
        roster_counts=roster_counts,
        taken_at=datetime.now(UTC),
        snapshot_transaction_id=str(transaction_id[0]),
        database=str(transaction_id[1]),
        host=host,
        port=port,
        org_id=org_id,
    )
