"""
Role: The CENSUS_V1 metric catalogue (contract §9, §16.5) — for each of the sixty-seven literal
      metric keys, the exact SQL that measures it, its unit and its result shape. Data only:
      this module runs nothing and opens no connection.
Used by: tools.mem01_verify.census (executes every spec against the R6 snapshot session) and,
      through it, the sealed oracle backend/tests/tools/mem01_verify/test_census.py.
Depends on: stdlib only (dataclasses, typing). The per-gate denominator table lives beside it in
      tools.mem01_verify.census_denominators, which this module does not import.
Key invariants:
  - `CENSUS_METRICS` carries EXACTLY the §16.5 keys, once each, in §16.5 order.
  - Every statement is a single read-only `SELECT`. Every statement over a tenant table carries
    an explicit `org_id = :org_id` predicate on top of the session's tenant binding
    (security.md layer 3); the four `schema_*` statements are catalogue reads and take no
    parameter.
  - Ordering is NOT expressed in SQL. Distributions come back unordered and the executor sorts
    them in Python by count descending then key ascending (§16.5), because the database's
    collation is not codepoint order and would make the emitted file locale-dependent.
  - Aggregates that sum are cast `::bigint`: `sum()` over `bigint` yields `numeric`, which
    arrives as a `Decimal` and is not JSON-serializable.
  - Byte metrics EXCLUDE NULL texts (§16.14); the redaction-scan cap is measured in Unicode
    scalars (`char_length`), never in octets (§5.1).
  - No statement selects a personal-data column: counts, hashes, lengths, statuses, MIME types,
    header KEYS and file extensions only (rule R5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CENSUS_VERSION = "CENSUS_V1"

CENSUS_FILENAME = "census.json"

# The fixed prefix `app.connectors.extraction.redact` writes in place of every secret. Embedded
# as a LIKE literal (Postgres LIKE knows only `%` and `_`, so the brackets are literal) so the
# emitted SQL is self-contained and needs no import to be re-run by hand.
REDACTION_PLACEHOLDER_PREFIX = "[REDACTED:"

# §5.1 / §16.9: a body longer than this many Unicode scalars was not fully scanned for secrets.
REDACT_SCAN_CAP_SCALARS = 2_000_000

MetricShape = Literal["scalar", "distribution", "rows"]


@dataclass(frozen=True)
class MetricSpec:
    """One census metric: its key, the statement that measures it, its unit and its shape.

    `shape` decides how the executor turns the result set into a JSON value:
      - `scalar`: one row, one column — the value itself.
      - `distribution`: rows of `(key, count)` — a list of `{"key", "count"}` objects.
      - `rows`: rows of `columns` — a list of objects with exactly those field names; the last
        column is always `count`.
    """

    key: str
    sql: str
    unit: str
    shape: MetricShape = "scalar"
    columns: tuple[str, ...] = ()


def _count(key: str, table: str, unit: str, predicate: str = "") -> MetricSpec:
    """A `count(*)` over one tenant table, optionally narrowed by `predicate` (` AND …`)."""
    return MetricSpec(
        key=key,
        sql=f"SELECT count(*)::bigint FROM {table} WHERE org_id = :org_id{predicate}",
        unit=unit,
    )


def _distinct(key: str, table: str, column: str, unit: str, predicate: str = "") -> MetricSpec:
    """A `count(DISTINCT column)` over one tenant table."""
    return MetricSpec(
        key=key,
        sql=(
            f"SELECT count(DISTINCT {column})::bigint FROM {table} "
            f"WHERE org_id = :org_id{predicate}"
        ),
        unit=unit,
    )


def _distribution(
    key: str, expression: str, table: str, unit: str, predicate: str = ""
) -> MetricSpec:
    """A `(key, count)` distribution over `expression`; the executor imposes the §16.5 order."""
    return MetricSpec(
        key=key,
        sql=(
            f"SELECT {expression} AS key, count(*)::bigint AS count FROM {table} "
            f"WHERE org_id = :org_id{predicate} GROUP BY 1"
        ),
        unit=unit,
        shape="distribution",
    )


def _group_members(key: str, table: str, column: str, unit: str, aggregate: str) -> MetricSpec:
    """A statistic over the repeated-value groups of `column` (groups, members, redundant)."""
    return MetricSpec(
        key=key,
        sql=(
            f"SELECT coalesce({aggregate}, 0)::bigint FROM ("
            f"SELECT count(*) AS members FROM {table} "
            f"WHERE org_id = :org_id AND {column} IS NOT NULL "
            f"GROUP BY {column} HAVING count(*) > 1) repeated"
        ),
        unit=unit,
    )


def _byte_metrics(prefix: str, table: str, column: str) -> tuple[MetricSpec, ...]:
    """The five `min / p50 / p90 / p99 / max` octet-length metrics of one stored text column.

    NULL texts are excluded (§16.14) — a column that stores nothing has no length, and counting
    it as zero would drag every percentile down. Percentiles are `percentile_disc` (§9): an
    OBSERVED byte length, never an interpolated one.
    """
    scope = f"FROM {table} WHERE org_id = :org_id AND {column} IS NOT NULL"
    length = f"octet_length({column})"
    percentiles = [
        MetricSpec(
            key=f"{prefix}_{name}",
            sql=(
                f"SELECT coalesce(percentile_disc({fraction}) WITHIN GROUP "
                f"(ORDER BY {length}), 0)::bigint {scope}"
            ),
            unit="bytes",
        )
        for name, fraction in (("p50", "0.5"), ("p90", "0.9"), ("p99", "0.99"))
    ]
    return (
        MetricSpec(f"{prefix}_min", f"SELECT coalesce(min({length}), 0)::bigint {scope}", "bytes"),
        *percentiles,
        MetricSpec(f"{prefix}_max", f"SELECT coalesce(max({length}), 0)::bigint {scope}", "bytes"),
    )


_EMAIL = "email_message"
_ATTACHMENT = "email_attachment"
# A header present but EMPTY carries no value: `lang_bootstrap` classes it `none` (§16.10),
# and counting it would make the value distribution sum to more than
# `emails_content_language_present`. Raw key presence stays visible in
# `emails_header_key_coverage`, so the empty-valued population is the difference.
_HAS_LANGUAGE_VALUE = " AND nullif(headers ->> 'Content-Language', '') IS NOT NULL"

# `references` is a reserved SQL keyword — the column must stay quoted everywhere.
_EMAIL_METRICS: tuple[MetricSpec, ...] = (
    _count("emails_total", _EMAIL, "emails"),
    _count(
        "emails_body_nonempty", _EMAIL, "emails", " AND body_text IS NOT NULL AND body_text <> ''"
    ),
    _count("emails_body_empty", _EMAIL, "emails", " AND (body_text IS NULL OR body_text = '')"),
    _count("emails_parse_failed", _EMAIL, "emails", " AND parse_status = 'failed'"),
    _count("emails_message_id_null", _EMAIL, "emails", " AND message_id IS NULL"),
    _distinct("emails_message_id_distinct", _EMAIL, "message_id", "message ids"),
    _group_members("emails_message_id_reused_groups", _EMAIL, "message_id", "groups", "count(*)"),
    _group_members(
        "emails_message_id_reused_members", _EMAIL, "message_id", "emails", "sum(members)"
    ),
    _count("emails_in_reply_to_present", _EMAIL, "emails", " AND in_reply_to IS NOT NULL"),
    _count(
        "emails_references_present",
        _EMAIL,
        "emails",
        ' AND "references" IS NOT NULL AND array_length("references", 1) > 0',
    ),
    _count("emails_content_language_present", _EMAIL, "emails", _HAS_LANGUAGE_VALUE),
    _distribution(
        "emails_content_language_values",
        "headers ->> 'Content-Language'",
        _EMAIL,
        "emails",
        _HAS_LANGUAGE_VALUE,
    ),
    _count("emails_language_null", _EMAIL, "emails", " AND language IS NULL"),
    _count("emails_sent_at_null", _EMAIL, "emails", " AND sent_at IS NULL"),
    _count("emails_received_at_null", _EMAIL, "emails", " AND received_at IS NULL"),
    _count(
        "emails_sent_eq_received",
        _EMAIL,
        "emails",
        " AND sent_at IS NOT NULL AND received_at IS NOT NULL AND sent_at = received_at",
    ),
    _count("emails_is_reply", _EMAIL, "emails", " AND is_reply"),
    _count("emails_has_attachments", _EMAIL, "emails", " AND has_attachments"),
    _count("emails_is_automated", _EMAIL, "emails", " AND is_automated"),
    _distribution("emails_direction", "coalesce(direction, '(null)')", _EMAIL, "emails"),
    _distribution(
        "emails_scope_distribution",
        "coalesce(visibility_scope, '(null)') || '/' || coalesce(origin_scope, '(null)')",
        _EMAIL,
        "emails",
    ),
    *_byte_metrics("emails_body_bytes", _EMAIL, "body_text"),
    _count(
        "emails_body_beyond_redact_cap",
        _EMAIL,
        "emails",
        f" AND body_text IS NOT NULL AND char_length(body_text) > {REDACT_SCAN_CAP_SCALARS}",
    ),
    MetricSpec(
        key="emails_dedup_dup_groups",
        sql=(
            "SELECT count(*)::bigint FROM (SELECT dedup_key FROM email_message "
            "WHERE org_id = :org_id GROUP BY dedup_key HAVING count(*) > 1) repeated"
        ),
        unit="groups",
    ),
    MetricSpec(
        key="emails_header_key_coverage",
        sql=(
            "SELECT header_key AS key, count(*)::bigint AS count FROM email_message message "
            "CROSS JOIN LATERAL jsonb_object_keys(message.headers) AS header_key "
            "WHERE message.org_id = :org_id GROUP BY 1"
        ),
        unit="emails",
        shape="distribution",
    ),
)

_ATTACHMENT_METRICS: tuple[MetricSpec, ...] = (
    _count("attachments_total", _ATTACHMENT, "attachments"),
    _count("attachments_with_text", _ATTACHMENT, "attachments", " AND extracted_text IS NOT NULL"),
    _count("attachments_inline", _ATTACHMENT, "attachments", " AND is_inline"),
    _distinct("attachments_content_hash_distinct", _ATTACHMENT, "content_hash", "hashes"),
    _group_members(
        "attachments_hash_dup_groups", _ATTACHMENT, "content_hash", "groups", "count(*)"
    ),
    _group_members(
        "attachments_hash_dup_members", _ATTACHMENT, "content_hash", "attachments", "sum(members)"
    ),
    _group_members(
        "attachments_hash_redundant_copies",
        _ATTACHMENT,
        "content_hash",
        "attachments",
        "sum(members - 1)",
    ),
    _distribution(
        "attachments_status_distribution", "extraction_status", _ATTACHMENT, "attachments"
    ),
    _distribution(
        "attachments_content_type_distribution",
        "coalesce(content_type, '(null)')",
        _ATTACHMENT,
        "attachments",
    ),
    MetricSpec(
        key="attachments_status_by_content_type",
        sql=(
            "SELECT extraction_status AS status, coalesce(content_type, '(null)') AS content_type, "
            "count(*)::bigint AS count FROM email_attachment "
            "WHERE org_id = :org_id AND extraction_status <> 'extracted' GROUP BY 1, 2"
        ),
        unit="attachments",
        shape="rows",
        columns=("status", "content_type", "count"),
    ),
    # The extension only — never the filename, which is personal data (R5).
    _distribution(
        "attachments_unsupported_extension_distribution",
        r"coalesce(lower(substring(filename from '\.([A-Za-z0-9]+)$')), '(none)')",
        _ATTACHMENT,
        "attachments",
        " AND extraction_status = 'unsupported_format'",
    ),
    *_byte_metrics("attachments_text_bytes", _ATTACHMENT, "extracted_text"),
    # The extraction cap is a STORED signal, not a recomputed constant: `truncated` is exactly
    # what the extractor records when it stopped short of the whole document.
    _count(
        "attachments_text_beyond_extraction_cap",
        _ATTACHMENT,
        "attachments",
        " AND extraction_status = 'truncated'",
    ),
    _distribution(
        "attachments_extractor_distribution",
        "coalesce(extractor_name, '(null)') || '@' || coalesce(extractor_version, '(null)')",
        _ATTACHMENT,
        "attachments",
    ),
)

_PEOPLE_METRICS: tuple[MetricSpec, ...] = (
    _count("persons_total", "person", "persons"),
    _count("person_emails_total", "person_email", "addresses"),
    MetricSpec(
        key="persons_multi_address",
        sql=(
            "SELECT count(*)::bigint FROM (SELECT person_id FROM person_email "
            "WHERE org_id = :org_id GROUP BY person_id HAVING count(*) > 1) multi"
        ),
        unit="persons",
    ),
    _count("person_aliases_total", "person_alias", "aliases"),
    _count("companies_total", "company", "companies"),
    _distribution("recipients_by_kind", "kind", "email_recipient", "recipients"),
    _distribution(
        "recipients_unlinked_by_kind",
        "kind",
        "email_recipient",
        "recipients",
        " AND person_id IS NULL",
    ),
)

_ACCESS_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec(
        key="acl_grants_by_object_and_provenance",
        sql=(
            "SELECT object_type, provenance, count(*)::bigint AS count FROM acl_grant "
            "WHERE org_id = :org_id GROUP BY 1, 2"
        ),
        unit="grants",
        shape="rows",
        columns=("object_type", "provenance", "count"),
    ),
    _count("acl_grants_revoked", "acl_grant", "grants", " AND revoked_at IS NOT NULL"),
    _distinct("acl_grant_holders_distinct", "acl_grant", "person_id", "persons"),
    _count("fact_provenance_total", "fact_provenance", "rows"),
    # audit_log carries a NULLABLE org_id (platform events belong to no tenant), so the tenant
    # predicate is load-bearing here rather than merely defensive.
    _count("audit_log_total", "audit_log", "rows"),
    _count("visibility_promotion_total", "visibility_promotion", "rows"),
    _count("principal_source_identity_total", "principal_source_identity", "rows"),
    _count(
        "redaction_bodies_with_placeholder",
        _EMAIL,
        "emails",
        f" AND body_text LIKE '%{REDACTION_PLACEHOLDER_PREFIX}%'",
    ),
    _count(
        "redaction_attachments_with_placeholder",
        _ATTACHMENT,
        "attachments",
        f" AND extracted_text LIKE '%{REDACTION_PLACEHOLDER_PREFIX}%'",
    ),
)

_SCHEMA_METRICS: tuple[MetricSpec, ...] = (
    # Read through `db.read_alembic_version` (§16.15): migration 0013 revokes every privilege on
    # `alembic_version` from `oneai_app`, so this one statement cannot run on the snapshot plane
    # and is issued in its own READ ONLY / REPEATABLE READ transaction on the global role.
    MetricSpec(
        key="schema_alembic_version",
        sql="SELECT version_num FROM alembic_version",
        unit="revision",
    ),
    MetricSpec(
        key="schema_pgvector_version",
        sql="SELECT coalesce(max(extversion), '') FROM pg_extension WHERE extname = 'vector'",
        unit="version",
    ),
    # pg_attribute rather than information_schema.columns: the latter hides relations the
    # connected role holds no privilege on, which would under-report vector columns.
    MetricSpec(
        key="schema_vector_columns",
        sql=(
            "SELECT count(*)::bigint FROM pg_attribute attribute "
            "JOIN pg_class relation ON relation.oid = attribute.attrelid "
            "JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace "
            "JOIN pg_type type ON type.oid = attribute.atttypid "
            "WHERE type.typname = 'vector' AND attribute.attnum > 0 "
            "AND NOT attribute.attisdropped AND relation.relkind IN ('r', 'p', 'm', 'v') "
            "AND namespace.nspname NOT IN ('pg_catalog', 'information_schema')"
        ),
        unit="columns",
    ),
    MetricSpec(
        key="schema_database_size_bytes",
        sql="SELECT pg_database_size(current_database())::bigint",
        unit="bytes",
    ),
)

CENSUS_METRICS: tuple[MetricSpec, ...] = (
    *_EMAIL_METRICS,
    *_ATTACHMENT_METRICS,
    *_PEOPLE_METRICS,
    *_ACCESS_METRICS,
    *_SCHEMA_METRICS,
)
