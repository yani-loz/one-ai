"""
Role: Seals fix-registry row A29 / contract §16.17(c) on the COV gate — a partial extraction is
      not delivered: the annex clause `scope_policy.delivered_requires.attachment` carries
      `partial_marker_absent: true` while `scope_policy.version` stays `v0` (a founder draft);
      `CorpusInput` accepts `structured_truncated` and a truncated workbook with text and full
      provenance is disposed `not_ready` where the same record untruncated is `delivered`; the
      corpus attachment query carries the COMPLETE aliased boolean projection
      `(extracted_data->>'truncated' = 'true') AS structured_truncated` — no cast between the
      comparison and the alias, since a text projection's `"false"` is truthy in Python — AND
      the row adapter materialises that column into `CorpusInput.structured_truncated`; and the
      fixture battery
      gains a truncated-workbook case disposed `not_ready` while every other record keeps
      `structured_truncated` False (no existing record changed meaning).
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.gates.gate_cov, .gates.context, .lock, .criteria and
      .fixtures.cov_scenarios (imported inside each test through the `instrument` loader); the
      criteria annex through the `criteria_yaml` fixture (pyyaml, independent of the
      instrument's loader) and `criteria_path`.
Key invariants:
  - The policy the dispositions are checked against is the annex as parsed by pyyaml (R12), and
    the two hand-built corpus rows differ in `structured_truncated` alone, so the disposition
    change is attributable to that one property.
  - The corpus read is driven through a FAKE snapshot session yielding attribute rows, so no
    database is opened; the row attribute the adapter must read is `structured_truncated`.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from tests.tools.mem01_verify.conftest import InstrumentLoader

WORKBOOK_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SPREADSHEET_CONTENT_TYPES = frozenset({WORKBOOK_CONTENT_TYPE, "application/vnd.ms-excel"})
HEX = "7c" * 32
ORG_ID = UUID("00000000-0000-4000-8000-0000000000aa")
TRUNCATED_PROJECTION = re.compile(  # the comparison AND its alias, with nothing (no cast) between
    r"\(\s*extracted_data\s*->>\s*'truncated'\s*=\s*'true'\s*\)\s+AS\s+structured_truncated"
)
COMPLETE_HANDOFF = {
    "content_type": WORKBOOK_CONTENT_TYPE,
    "is_inline": False,
    "extraction_status": "extracted",
    "text_present": True,
    "extractor_name_present": True,
    "extractor_version_present": True,
}


def _same_disposition(actual: object, expected: object) -> bool:
    return actual == expected or getattr(actual, "value", actual) == getattr(
        expected, "value", expected
    )


def _workbook_input(gate_cov: object, *, structured_truncated: bool) -> object:
    """An `extracted` workbook with text and full provenance — the ruled shape of §16.17(c)."""
    return gate_cov.CorpusInput(  # type: ignore[attr-defined]
        kind="attachment",
        input_id="x",
        structured_truncated=structured_truncated,
        **COMPLETE_HANDOFF,
    )


def test_the_annex_attachment_clause_requires_the_partial_marker_absent_under_v0(
    criteria_yaml: dict,
) -> None:
    policy = criteria_yaml["scope_policy"]

    assert policy["version"] == "v0" and policy["founder_draft"] is True
    assert policy["delivered_requires"]["attachment"]["partial_marker_absent"] is True


def test_a_truncated_workbook_is_not_ready_and_the_same_record_untruncated_is_delivered(
    instrument: InstrumentLoader, criteria_yaml: dict
) -> None:
    gate_cov = instrument("gates.gate_cov")
    policy = criteria_yaml["scope_policy"]

    truncated = gate_cov.dispose(_workbook_input(gate_cov, structured_truncated=True), policy)
    delivered = gate_cov.dispose(_workbook_input(gate_cov, structured_truncated=False), policy)

    assert _same_disposition(truncated, "not_ready"), truncated
    assert _same_disposition(delivered, "delivered"), delivered  # positive control


def test_the_corpus_attachment_query_reads_the_truncated_marker(
    instrument: InstrumentLoader,
) -> None:
    gate_cov = instrument("gates.gate_cov")

    sql = str(gate_cov._ATTACHMENT_SQL)

    assert TRUNCATED_PROJECTION.search(sql), "no complete aliased boolean projection"


class _FakeResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return list(self._rows)


class _FakeSnapshotSession:
    """Answers the gate's two corpus queries with attribute rows; opens nothing."""

    def __init__(self, emails: list[object], attachments: list[object]) -> None:
        self._emails, self._attachments = emails, attachments

    async def execute(self, statement: object, parameters: object = None) -> _FakeResult:
        rows = self._attachments if "email_attachment" in str(statement) else self._emails
        return _FakeResult(rows)


def _gate_context(
    instrument: InstrumentLoader, criteria_path: Path, session: object, root: Path
) -> object:
    """A DB-free context whose release and report paths lie under the test's tmp_path."""
    release = instrument("lock").ReleaseInfo(
        path=root / "release",
        name="step1-gold-v1",
        state="draft",
        lock_sha256=HEX,
        manifest={},
        criteria_path=criteria_path,
        visible_files_verified=0,
        hidden_files_verified=0,
    )

    @asynccontextmanager
    async def snapshot() -> AsyncIterator[object]:  # an async generator: the gate enters it
        yield session

    return instrument("gates.context").GateContext(
        release=release,
        criteria=instrument("criteria").load_criteria(criteria_path),
        run_kind="tuning",
        split_evaluated="optimization",
        org_id=ORG_ID,
        corpus=None,
        corpus_snapshot=snapshot,
        probe=None,
        fixtures_digest=HEX,
        report_dir=root / "release" / "reports" / "unused",
        hidden_root=None,
        versions={},
    )


async def test_the_row_adapter_materialises_the_truncated_marker_into_corpus_inputs(
    instrument: InstrumentLoader, criteria_path: Path, criteria_yaml: dict, tmp_path: Path
) -> None:
    gate_cov = instrument("gates.gate_cov")
    attachments = [
        SimpleNamespace(id="att-truncated", structured_truncated=True, **COMPLETE_HANDOFF),
        SimpleNamespace(id="att-complete", structured_truncated=False, **COMPLETE_HANDOFF),
    ]
    emails = [SimpleNamespace(id="email-1", parse_status="parsed", text_present=True)]
    session = _FakeSnapshotSession(emails, attachments)
    ctx = _gate_context(instrument, criteria_path, session, tmp_path)
    policy = criteria_yaml["scope_policy"]

    inputs = await gate_cov._read_corpus_inputs(ctx)

    by_id = {scoped.input_id: scoped for scoped in inputs}
    assert set(by_id) == {"email-1", "att-truncated", "att-complete"}
    assert by_id["att-truncated"].structured_truncated is True
    assert by_id["att-complete"].structured_truncated is False
    assert by_id["email-1"].structured_truncated is False
    assert _same_disposition(gate_cov.dispose(by_id["att-truncated"], policy), "not_ready")
    assert _same_disposition(gate_cov.dispose(by_id["att-complete"], policy), "delivered")
    assert _same_disposition(gate_cov.dispose(by_id["email-1"], policy), "delivered")


def test_the_battery_gains_a_truncated_workbook_case_and_no_other_record_changes_meaning(
    instrument: InstrumentLoader,
) -> None:
    scenarios = instrument("fixtures.cov_scenarios").COV_SCENARIOS

    truncated = [s for s in scenarios if s.structured_truncated is True]
    truncated_ids = {s.case_id for s in truncated}

    assert truncated, "no scenario carries structured_truncated=True"
    assert all(s.kind == "attachment" for s in truncated)
    assert all(s.expected.disposition == "not_ready" for s in truncated)
    assert any(  # the ruled case: extracted, text and provenance present, truncated payload
        s.extraction_status == "extracted"
        and s.text_present
        and s.extractor_name_present
        and s.extractor_version_present
        and str(s.content_type).split(";", 1)[0].strip().lower() in SPREADSHEET_CONTENT_TYPES
        for s in truncated
    )
    for scenario in scenarios:
        if scenario.case_id not in truncated_ids:
            assert scenario.structured_truncated is False, scenario.case_id
