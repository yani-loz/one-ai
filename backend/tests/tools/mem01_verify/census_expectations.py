"""
Role: The literal CENSUS_V1 metric key list of contract §16.5 and the small-org values the
      seeding spec determines (seeding.py `seed_small_org`), so the census seals compare against
      numbers written down here from the row list, never read back from the database or the
      instrument (R12).
Used by: test_census.py.
Depends on: stdlib only.
Key invariants:
  - Every number below follows from seed_small_org's rows by hand; a change to the seeder must be
    mirrored here. Distributions list only their non-zero entries in §16.5 order.
"""

from __future__ import annotations

CENSUS_METRIC_KEYS = (
    "emails_total",
    "emails_body_nonempty",
    "emails_body_empty",
    "emails_parse_failed",
    "emails_message_id_null",
    "emails_message_id_distinct",
    "emails_message_id_reused_groups",
    "emails_message_id_reused_members",
    "emails_in_reply_to_present",
    "emails_references_present",
    "emails_content_language_present",
    "emails_content_language_values",
    "emails_language_null",
    "emails_sent_at_null",
    "emails_received_at_null",
    "emails_sent_eq_received",
    "emails_is_reply",
    "emails_has_attachments",
    "emails_is_automated",
    "emails_direction",
    "emails_scope_distribution",
    "emails_body_bytes_min",
    "emails_body_bytes_p50",
    "emails_body_bytes_p90",
    "emails_body_bytes_p99",
    "emails_body_bytes_max",
    "emails_body_beyond_redact_cap",
    "emails_dedup_dup_groups",
    "emails_header_key_coverage",
    "attachments_total",
    "attachments_with_text",
    "attachments_inline",
    "attachments_content_hash_distinct",
    "attachments_hash_dup_groups",
    "attachments_hash_dup_members",
    "attachments_hash_redundant_copies",
    "attachments_status_distribution",
    "attachments_content_type_distribution",
    "attachments_status_by_content_type",
    "attachments_unsupported_extension_distribution",
    "attachments_text_bytes_min",
    "attachments_text_bytes_p50",
    "attachments_text_bytes_p90",
    "attachments_text_bytes_p99",
    "attachments_text_bytes_max",
    "attachments_text_beyond_extraction_cap",
    "attachments_extractor_distribution",
    "persons_total",
    "person_emails_total",
    "persons_multi_address",
    "person_aliases_total",
    "companies_total",
    "recipients_by_kind",
    "recipients_unlinked_by_kind",
    "acl_grants_by_object_and_provenance",
    "acl_grants_revoked",
    "acl_grant_holders_distinct",
    "fact_provenance_total",
    "audit_log_total",
    "visibility_promotion_total",
    "principal_source_identity_total",
    "redaction_bodies_with_placeholder",
    "redaction_attachments_with_placeholder",
    "schema_alembic_version",
    "schema_pgvector_version",
    "schema_vector_columns",
    "schema_database_size_bytes",
)

# Metrics whose value is a `[{"key", "count"}, ...]` list (§16.5 second paragraph).
DISTRIBUTION_KEYS = (
    "emails_content_language_values",
    "emails_direction",
    "emails_scope_distribution",
    "emails_header_key_coverage",
    "attachments_status_distribution",
    "attachments_content_type_distribution",
    "attachments_unsupported_extension_distribution",
    "attachments_extractor_distribution",
    "recipients_by_kind",
    "recipients_unlinked_by_kind",
)

BYTES_PERCENTILE_KEYS = ("min", "p50", "p90", "p99", "max")

# seed_small_org: six emails (E3 parse-failed with NULL body/subject; E1 and E6 reuse m1;
# E2 replies to m1, E5 replies to an external id; E2's received_at differs from sent_at;
# E1-E5 carry has_attachments), five attachments (A1 extracted pdf and A2 pending pdf share a
# hash; A3/A4 inline pngs share a hash; A5 unsupported msword), three persons with three
# addresses (P1 has two), three unrevoked grants for P1, six recipient rows.
SMALL_ORG_EXACT: dict[str, int] = {
    "emails_total": 6,
    "emails_body_nonempty": 5,
    "emails_body_empty": 1,
    "emails_parse_failed": 1,
    "emails_message_id_null": 0,
    "emails_message_id_distinct": 5,
    "emails_message_id_reused_groups": 1,
    "emails_message_id_reused_members": 2,
    "emails_in_reply_to_present": 2,
    "emails_language_null": 6,
    "emails_sent_at_null": 0,
    "emails_received_at_null": 0,
    "emails_sent_eq_received": 5,
    "emails_is_reply": 1,
    "emails_has_attachments": 5,
    "emails_is_automated": 0,
    "emails_body_beyond_redact_cap": 0,
    "emails_dedup_dup_groups": 0,
    "attachments_total": 5,
    "attachments_with_text": 1,
    "attachments_inline": 2,
    "attachments_content_hash_distinct": 3,
    "attachments_hash_dup_groups": 2,
    "attachments_hash_dup_members": 4,
    "attachments_hash_redundant_copies": 2,
    "attachments_text_beyond_extraction_cap": 0,
    "persons_total": 3,
    "person_emails_total": 3,
    "persons_multi_address": 1,
    "person_aliases_total": 0,
    "companies_total": 0,
    "acl_grants_revoked": 0,
    "acl_grant_holders_distinct": 1,
    "fact_provenance_total": 0,
    "visibility_promotion_total": 0,
    "principal_source_identity_total": 0,
    "redaction_bodies_with_placeholder": 0,
    "redaction_attachments_with_placeholder": 0,
}

# Non-zero entries in §16.5 order (count descending, then key ascending).
SMALL_ORG_DISTRIBUTIONS: dict[str, list[tuple[str, int]]] = {
    "attachments_content_type_distribution": [
        ("application/pdf", 2),
        ("image/png", 2),
        ("application/msword", 1),
    ],
    "attachments_status_distribution": [
        ("skipped_nondocument", 2),
        ("extracted", 1),
        ("pending", 1),
        ("unsupported_format", 1),
    ],
    "emails_header_key_coverage": [("Content-Language", 5), ("Date", 1)],
    "recipients_by_kind": [("to", 4), ("bcc", 1), ("cc", 1)],
    "recipients_unlinked_by_kind": [("bcc", 1), ("to", 1)],
}

# status x content_type for every non-`extracted` status (order unspecified; compared as a set).
SMALL_ORG_STATUS_BY_CONTENT_TYPE = {
    ("pending", "application/pdf", 1),
    ("skipped_nondocument", "image/png", 2),
    ("unsupported_format", "application/msword", 1),
}

# A1's extracted text is "OracleBodyText attachment one" (29 ASCII bytes), the only text.
ATTACHMENT_TEXT_BYTES = 29
ACL_GRANTS_TOTAL = 3
UNSUPPORTED_EXTENSION_FORMS = {"doc", ".doc"}
