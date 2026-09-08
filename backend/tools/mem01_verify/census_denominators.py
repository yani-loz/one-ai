"""
Role: The CENSUS_V1 per-gate denominator table (contract §9, last bullet) — for each of the
      seventeen gates, which census metric measures each of its denominator roles today, and
      which roles have no measured denominator yet (`not_yet_labeled`). Data only: this module
      runs nothing and opens no connection.
Used by: tools.mem01_verify.census (emits the table beside the metrics and DERIVES every
      `Metric.denominator_for` from it) and, through it, the sealed oracle
      backend/tests/tools/mem01_verify/test_census.py.
Depends on: tools.mem01_verify.statuses (`GATE_NAMES`, the single §1.3 gate roster) — a wave-1
      leaf that imports only `.exceptions`, so the census stays free of the wave-3
      `tools.mem01_verify.gates` package, which this module deliberately does NOT import.
Key invariants:
  - `DENOMINATORS_BY_GATE` covers EXACTLY the seventeen `CENSUS_GATES`, each with at least one
    denominator role; `census._validate_denominator_table` fails the census otherwise.
  - Every value is either a §16.5 census metric key or the literal `not_yet_labeled`, which
    reads "no census-measured denominator exists for this role today" — it covers both
    unlabeled H evidence (QS / NF / LANG / RET) and denominators that live in the fixture
    batteries.
  - This table is the SINGLE source of truth for gate roles: `Metric.denominator_for` is
    derived from it, so the emitted table and the per-metric roles cannot disagree.
"""

from __future__ import annotations

from tools.mem01_verify.statuses import GATE_NAMES

NOT_YET_LABELED = "not_yet_labeled"
"""A denominator with no census metric today (unlabeled H evidence, or an F battery)."""

# The seventeen gates in the frozen §1.3 order, taken from the one module that writes the
# roster down. `census._validate_denominator_table` still checks the table against it, so a
# gate added to the roster without a denominator row fails the census rather than passing.
CENSUS_GATES: tuple[str, ...] = GATE_NAMES


# §9 last bullet: for each of the seventeen gates, which census metrics are its denominators
# today and which denominators do not exist yet. The KEY names the denominator role; the VALUE
# is the metric that measures it, or `not_yet_labeled` when nothing measures it in Stage A.
DENOMINATORS_BY_GATE: dict[str, dict[str, str]] = {
    "QS": {"labeled_quote_spans": NOT_YET_LABELED, "emails_with_a_body": "emails_body_nonempty"},
    "CH": {"chunks": NOT_YET_LABELED, "emails_with_a_body": "emails_body_nonempty"},
    "NF": {"labeled_noise_examples": NOT_YET_LABELED, "attachments": "attachments_total"},
    "LANG": {
        "labeled_language_examples": NOT_YET_LABELED,
        "emails": "emails_total",
        "emails_without_a_language": "emails_language_null",
    },
    "IDEM": {"replay_scenarios": NOT_YET_LABELED, "emails": "emails_total"},
    "VIS": {
        "bcc_recipients": "recipients_by_kind",
        "grants": "acl_grants_by_object_and_provenance",
        "promotions": "visibility_promotion_total",
    },
    "ERASE": {"erasure_requests": NOT_YET_LABELED, "emails": "emails_total"},
    "RET": {"qrels": NOT_YET_LABELED, "emails": "emails_total"},
    "COV": {
        "delivered_documents": "attachments_total",
        "dispositions_by_status": "attachments_status_distribution",
        "parser_failures": "emails_parse_failed",
    },
    "FID": {
        "fidelity_cases": NOT_YET_LABELED,
        "attachments_with_stored_text": "attachments_with_text",
    },
    "THR": {"threads": NOT_YET_LABELED, "emails": "emails_total"},
    "TIME": {
        "emails_with_a_date_header": "emails_header_key_coverage",
        "emails_without_a_sent_at": "emails_sent_at_null",
    },
    "IDENT": {
        "persons": "persons_total",
        "person_addresses": "person_emails_total",
        "aliases": "person_aliases_total",
    },
    "RED": {
        "email_bodies": "emails_body_nonempty",
        "attachment_texts": "attachments_with_text",
        "canaries": NOT_YET_LABELED,
    },
    "ATTR": {"forwarded_segments": NOT_YET_LABELED, "emails": "emails_total"},
    "SNAP": {
        "email_bodies": "emails_total",
        "attachment_texts": "attachments_with_text",
    },
    "EMB": {"embeddings": NOT_YET_LABELED, "vector_columns": "schema_vector_columns"},
}
