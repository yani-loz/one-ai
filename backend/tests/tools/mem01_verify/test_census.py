"""
Role: Seals CENSUS_V1 (contract §9, §1.4, §16.5) on the probe corpus — exactly the literal metric
      keys, each carrying its SQL, unit and denominator role; distributions as count-ordered
      `{key, count}` lists; the small org's counts and distributions as the seeding spec
      determines them; discrete percentiles in order; the schema-state metrics; a
      denominators-by-gate table over all 17 gates; the docs_reconciliation entries; the emitted
      census.json; determinism.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.census and .db (imported inside each test); the session probe
      corpus; tests.tools.mem01_verify.census_expectations (values from the seeding spec);
      tests.tools.mem01_verify.reference (head revision, distribution shape helpers).
Key invariants:
  - No expected number is read from the instrument or the database (R12).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from tests.tools.mem01_verify import census_expectations as expected
from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import (
    GATE_NAMES,
    SESSION_LOOP,
    InstrumentLoader,
    ProbeCorpusFactory,
)

QUOTED_EXACT = {5893, 8454, 2850, 5, 88}
MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "app" / "db" / "migrations" / "versions"


async def _census(instrument: InstrumentLoader, corpus: object, org_id: object) -> object:
    db = instrument("db")
    census = instrument("census")
    async with db.readonly_corpus_snapshot(org_id, database=corpus.database) as conn:  # type: ignore[attr-defined]
        return await census.take_census(conn, org_id)


def _value(result: object, key: str) -> object:
    return result.metrics[key].value  # type: ignore[attr-defined]


@SESSION_LOOP
async def test_take_census_metrics_are_exactly_the_literal_keys_with_sql_unit_and_gate_roles(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory
) -> None:
    corpus = await probe_corpus()

    result = await _census(instrument, corpus, corpus.small.org_id)

    assert result.version == "CENSUS_V1" and result.org_id == corpus.small.org_id
    assert re.fullmatch(reference.HEX64_PATTERN, result.corpus_digest)
    assert isinstance(result.taken_at, datetime) and result.taken_at.tzinfo is not None
    assert set(result.metrics) == set(expected.CENSUS_METRIC_KEYS)
    for metric in result.metrics.values():
        json.dumps(metric.value)
        assert isinstance(metric.sql, str) and "select" in metric.sql.lower()
        assert isinstance(metric.unit, str) and metric.unit
        assert isinstance(metric.denominator_for, tuple)
        assert set(metric.denominator_for) <= set(GATE_NAMES)


@SESSION_LOOP
async def test_distribution_metrics_are_count_ordered_key_count_lists(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory
) -> None:
    corpus = await probe_corpus()

    result = await _census(instrument, corpus, corpus.small.org_id)

    for key in expected.DISTRIBUTION_KEYS:
        value = _value(result, key)
        assert reference.is_count_distribution(value), key
        assert reference.is_ordered_distribution(value), (key, value)
    by_type = _value(result, "attachments_status_by_content_type")
    assert isinstance(by_type, list) and by_type
    assert all(set(item) == {"status", "content_type", "count"} for item in by_type)


@SESSION_LOOP
async def test_small_org_counts_match_the_seeding_spec(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory
) -> None:
    corpus = await probe_corpus()

    result = await _census(instrument, corpus, corpus.small.org_id)

    mismatches = {
        key: (_value(result, key), value)
        for key, value in expected.SMALL_ORG_EXACT.items()
        if _value(result, key) != value
    }
    assert mismatches == {}
    assert _value(result, "emails_total") == corpus.small.email_count
    assert _value(result, "attachments_total") == corpus.small.attachment_count


@SESSION_LOOP
async def test_small_org_distributions_match_the_seeding_spec(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory
) -> None:
    corpus = await probe_corpus()

    result = await _census(instrument, corpus, corpus.small.org_id)

    for key, entries in expected.SMALL_ORG_DISTRIBUTIONS.items():
        assert reference.nonzero_entries(_value(result, key)) == entries, key
    by_type = {
        (item["status"], item["content_type"], item["count"])
        for item in _value(result, "attachments_status_by_content_type")
        if item["count"] > 0
    }
    assert by_type == expected.SMALL_ORG_STATUS_BY_CONTENT_TYPE
    extensions = reference.nonzero_entries(
        _value(result, "attachments_unsupported_extension_distribution")
    )
    assert len(extensions) == 1 and extensions[0][1] == 1
    assert extensions[0][0] in expected.UNSUPPORTED_EXTENSION_FORMS
    grants = _value(result, "acl_grants_by_object_and_provenance")
    grant_total = sum(reference.collect_ints(grants)) if isinstance(grants, list | dict) else grants
    assert grant_total == expected.ACL_GRANTS_TOTAL


@SESSION_LOOP
async def test_byte_length_percentiles_are_observed_values_in_order(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory
) -> None:
    corpus = await probe_corpus()

    result = await _census(instrument, corpus, corpus.small.org_id)

    bodies = [_value(result, f"emails_body_bytes_{p}") for p in expected.BYTES_PERCENTILE_KEYS]
    texts = [_value(result, f"attachments_text_bytes_{p}") for p in expected.BYTES_PERCENTILE_KEYS]
    assert all(isinstance(v, int) and not isinstance(v, bool) for v in bodies + texts)
    assert bodies == sorted(bodies) and bodies[0] >= 0
    assert texts == [expected.ATTACHMENT_TEXT_BYTES] * 5


@SESSION_LOOP
async def test_schema_state_metrics_name_the_head_revision_and_no_vector_columns(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory
) -> None:
    corpus = await probe_corpus()

    result = await _census(instrument, corpus, corpus.small.org_id)

    assert _value(result, "schema_alembic_version") == reference.repository_head_revision(
        MIGRATIONS_DIR
    )
    pgvector = _value(result, "schema_pgvector_version")
    assert isinstance(pgvector, str) and pgvector
    assert not _value(result, "schema_vector_columns")
    size = _value(result, "schema_database_size_bytes")
    assert isinstance(size, int) and size > 0


@SESSION_LOOP
async def test_denominators_by_gate_cover_all_17_and_name_existing_metrics(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory
) -> None:
    corpus = await probe_corpus()

    result = await _census(instrument, corpus, corpus.small.org_id)

    table = result.denominators_by_gate
    assert set(table) == set(GATE_NAMES)
    for gate in ("QS", "NF", "LANG", "RET"):
        assert "not_yet_labeled" in set(table[gate].values())
    for gate, denominators in table.items():
        assert denominators, gate
        for name in denominators.values():
            assert name == "not_yet_labeled" or name in result.metrics, (gate, name)
    assert any(name in result.metrics for name in table["COV"].values())


@SESSION_LOOP
async def test_docs_reconciliation_marks_every_quoted_number_against_the_measured_value(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory
) -> None:
    corpus = await probe_corpus()

    result = await _census(instrument, corpus, corpus.small.org_id)

    entries = list(result.docs_reconciliation)
    assert entries and all({"quoted", "measured", "match"} <= set(entry) for entry in entries)
    quoted_numbers = {entry["quoted"] for entry in entries if isinstance(entry["quoted"], int)}
    assert QUOTED_EXACT <= quoted_numbers
    assert any(
        isinstance(entry["quoted"], str) and entry["quoted"].endswith("%") for entry in entries
    )
    # a six-email org matches none of the documented corpus numbers
    assert all(entry["match"] is False for entry in entries)
    assert all(isinstance(entry["match"], bool) for entry in entries)


@SESSION_LOOP
async def test_write_census_emits_the_versioned_json(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory, tmp_path: Path
) -> None:
    corpus = await probe_corpus()
    census = instrument("census")
    result = await _census(instrument, corpus, corpus.big.org_id)

    path = census.write_census(result, tmp_path)

    assert path.name == "census.json" and path.is_relative_to(tmp_path)
    document = reference.read_json(path)
    assert document["census_version"] == "CENSUS_V1"
    assert document["corpus_digest"] == result.corpus_digest
    assert document["org_id"] == str(corpus.big.org_id) and document["taken_at"]
    assert set(document["metrics"]) == set(expected.CENSUS_METRIC_KEYS)
    assert all(
        {"value", "sql", "denominator_for", "unit"} <= set(metric)
        for metric in document["metrics"].values()
    )
    assert document["docs_reconciliation"]
    assert document["metrics"]["emails_total"]["value"] == corpus.big.email_count
    assert document["metrics"]["attachments_total"]["value"] == corpus.big.attachment_count


@SESSION_LOOP
async def test_take_census_is_deterministic_over_an_unchanged_org(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory
) -> None:
    corpus = await probe_corpus()

    first = await _census(instrument, corpus, corpus.small.org_id)
    second = await _census(instrument, corpus, corpus.small.org_id)

    volatile = "schema_database_size_bytes"  # §16.18: informational, never deterministic
    assert {k: (m.value, m.sql) for k, m in first.metrics.items() if k != volatile} == {
        k: (m.value, m.sql) for k, m in second.metrics.items() if k != volatile
    }
    assert volatile in first.metrics and volatile in second.metrics
    size_sql = "SELECT pg_database_size(current_database())::bigint"  # fixed by §16.18(a)
    for census in (first, second):
        assert census.metrics[volatile].sql == size_sql
        value = census.metrics[volatile].value
        assert isinstance(value, int) and not isinstance(value, bool)
        assert value > 0
    assert first.corpus_digest == second.corpus_digest
