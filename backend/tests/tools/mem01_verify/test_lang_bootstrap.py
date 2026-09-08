"""
Role: Seals LANG_BOOTSTRAP_V1 (contract §8) — the header-tag classification table and the
      per-org emitter (records, counts, full-precision coverage, the "hint, not truth" statement)
      on the probe database's six-email org.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.lang_bootstrap and .db (imported inside each test); the session
      probe corpus (conftest.probe_corpus) whose expected classes were fixed at seeding time.
Key invariants:
  - Expected classes come from seeding.py's spec, never from the instrument's output.
  - DB tests run on the session loop (loop_scope="session"); they open only the probe.
"""

from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import SESSION_LOOP, InstrumentLoader, ProbeCorpusFactory


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("bg", "bg"),
        ("BG", "bg"),
        ("bg-BG", "bg"),
        ("bg-BG, en", "bg"),
        (" bg ", "bg"),
        ("en", "en"),
        ("en-US", "en"),
        ("en-GB", "en"),
        ("EN-gb", "en"),
        (" en , bg", "en"),
        ("de", "other"),
        ("x-klingon", "other"),
        ("und", "other"),
        ("ru-RU", "other"),
        ("", "none"),
        (None, "none"),
        ("   ", "none"),
    ],
)
def test_classify_content_language_table(
    instrument: InstrumentLoader, header: str | None, expected: str
) -> None:
    lang_bootstrap = instrument("lang_bootstrap")

    assert lang_bootstrap.classify_content_language(header) == expected


def test_version_constant(instrument: InstrumentLoader) -> None:
    assert instrument("lang_bootstrap").LANG_BOOTSTRAP_VERSION == "LANG_BOOTSTRAP_V1"


@SESSION_LOOP
async def test_bootstrap_language_emits_records_counts_and_full_precision_coverage(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory, tmp_path: Path
) -> None:
    corpus = await probe_corpus()
    db = instrument("db")
    lang_bootstrap = instrument("lang_bootstrap")
    small = corpus.small

    async with db.readonly_corpus_snapshot(small.org_id, database=corpus.database) as conn:
        summary = await lang_bootstrap.bootstrap_language(conn, small.org_id, tmp_path)

    assert summary.version == "LANG_BOOTSTRAP_V1"
    assert dict(summary.counts) == {"bg": 2, "en": 1, "other": 1, "none": 2}
    assert summary.coverage == 4 / 6
    assert summary.records_path.name == "lang_bootstrap.jsonl"
    records = reference.read_jsonl(summary.records_path)
    assert all(
        {"email_id", "header_value_normalized", "bootstrap_class"} <= set(record)
        for record in records
    )
    assert {UUID(record["email_id"]): record["bootstrap_class"] for record in records} == dict(
        small.lang_class_by_email
    )
    assert len(records) == small.email_count  # only this org, every email once
    normalized = {UUID(r["email_id"]): r["header_value_normalized"] for r in records}
    ids = small.email_ids
    # §16.10: the first comma-separated tag, stripped and lowercased in full
    assert normalized[ids[0]] == "bg" and normalized[ids[1]] == "en-us"
    assert normalized[ids[2]] == "de" and normalized[ids[5]] == "bg-bg"
    assert normalized[ids[4]] == ""


@SESSION_LOOP
async def test_bootstrap_summary_carries_version_and_the_hint_statement(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory, tmp_path: Path
) -> None:
    corpus = await probe_corpus()
    db = instrument("db")
    lang_bootstrap = instrument("lang_bootstrap")

    async with db.readonly_corpus_snapshot(corpus.small.org_id, database=corpus.database) as conn:
        await lang_bootstrap.bootstrap_language(conn, corpus.small.org_id, tmp_path)

    summaries = [
        path
        for path in reference.rglob_files(tmp_path, "*.json")
        if "LANG_BOOTSTRAP_V1" in reference.read_text(path)
    ]
    assert len(summaries) == 1
    text = reference.read_text(summaries[0]).lower()
    assert "hint" in text and re.search(r"stage[ -]b", text)
    assert '"coverage"' in text and '"bg"' in text
