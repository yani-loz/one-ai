"""
Role: The TIME gate (timestamp honesty) of contract §11 — scores the public `Date`-header battery
      (`fixtures.time_cases`) against what the shipped date parser actually returns, and compares
      the corpus's stored `sent_at` with the retained `Date` header on at least 100 rows. Both
      criteria are evaluable in Stage A; the fixture criterion FAILS because naive and `-0000`
      headers are stamped UTC by the parser instead of declaring an unknown zone.
Used by: `tools.mem01_verify.gates.registry` (`evaluate`); the pure surface `score_case` is sealed
      by `tests/tools/mem01_verify/test_gate_scoring.py`, the gate outcome by
      `test_gates_stage_a.py`.
Depends on: `tools.mem01_verify.gates.context`, `.criteria`, `.statuses`,
      `.fixtures.time_cases` (data only) and `app.connectors.imap.parsing.headers.parse_date` —
      the measured component, invoked ONLY to obtain the ACTUAL result (R12).
Key invariants:
  - R12: no expected value ever comes from `parse_date`. Fixture expectations are the battery's
    own; the corpus reference instant is computed with the STDLIB `email.utils` parser and an
    independent zone-knowledge test, never with the component under measurement.
  - A header whose zone is not knowable (`-0000`, absent, an unrecognised alphabetic zone) fixes
    no instant: a stored non-null `sent_at` on such a row is an undisclosed assumption and counts
    against the comparison criterion.
  - Aggregates only reach the block: counts, never a header value, an address or a subject (R5).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from email.utils import parsedate_to_datetime

from sqlalchemy import text

from tools.mem01_verify.criteria import Criterion
from tools.mem01_verify.fixtures.time_cases import TIME_CASES, TimeCase
from tools.mem01_verify.gates.context import (
    CaseVerdict,
    GateContext,
    GateResult,
    criterion_entry,
    criterion_status,
    write_gate_report,
)
from tools.mem01_verify.statuses import ERROR, derive_gate_status

GATE = "TIME"
#: §12: this gate never touches `ctx.probe`, so it alone never leases a probe.
NEEDS_PROBE: bool = False
FIXTURES_CRITERION_ID = "time.fixtures"
COMPARISON_CRITERION_ID = "time.header_comparison"

#: The state a component declares when the header fixes no instant, and when it is not a date.
UNKNOWN_ZONE = "unknown_zone"
MALFORMED = "malformed"

_GATE_REASON = (
    "timestamp honesty is measured in full: the fixture battery fails because a header without "
    "usable zone information is stamped UTC instead of declaring an unknown zone"
)
_NO_CORPUS_REASON = "this run never opened the corpus, so no header comparison was possible (R2)"

#: RFC 5322 4.3 obs-zone names that DO carry a definite offset; every other alphabetic zone does
#: not, and `-0000` explicitly asserts that the local zone is unknown (RFC 5322 3.3).
_OBSOLETE_ZONE_OFFSETS: Mapping[str, int] = {
    "UT": 0,
    "GMT": 0,
    "EST": -5 * 3600,
    "EDT": -4 * 3600,
    "CST": -6 * 3600,
    "CDT": -5 * 3600,
    "MST": -7 * 3600,
    "MDT": -6 * 3600,
    "PST": -8 * 3600,
    "PDT": -7 * 3600,
}
_NUMERIC_ZONE = re.compile(r"([+-]\d{4})\s*(?:\(.*\))?\s*$")
_ALPHABETIC_ZONE = re.compile(r"\b([A-Za-z]{2,5})\s*(?:\(.*\))?\s*$")

_DATE_HEADER_SQL = text(
    "SELECT headers ->> 'Date' AS date_header, sent_at "
    "FROM email_message "
    "WHERE org_id = :org_id AND headers ->> 'Date' IS NOT NULL "
    "ORDER BY id"
)


def score_case(case: TimeCase, actual: datetime | str | None) -> CaseVerdict:
    """Score one `Date`-header fixture against the instant or state the RFC prescribes (§16.11).

    Args:
        case: The fixture record carrying the independently specified expectation.
        actual: What the measured component produced — a timezone-aware `datetime` when it
            claimed an instant, the state string it declared (`unknown_zone` / `malformed`)
            when it refused, or None when it produced nothing at all.

    Returns:
        A `CaseVerdict`. An expectation carrying `instant_utc` passes only on an aware datetime
        equal to that instant; an expectation carrying `state` passes only on that exact state
        string. Every failure names the deviation.
    """
    expected_instant = case.expected.get("instant_utc")
    if isinstance(expected_instant, str):
        return _score_instant(case, expected_instant, actual)
    expected_state = case.expected.get("state")
    if actual == expected_state:
        return CaseVerdict(case.case_id, case.criterion_id, True, ())
    defect = f"declared {actual!r} where the RFC fixes the state {expected_state!r}"
    return CaseVerdict(case.case_id, case.criterion_id, False, (defect,))


def _score_instant(
    case: TimeCase, expected_instant: str, actual: datetime | str | None
) -> CaseVerdict:
    """Score a fixture whose zone is known, so the RFC fixes exactly one instant."""
    expected = datetime.fromisoformat(expected_instant.replace("Z", "+00:00"))
    if not isinstance(actual, datetime):
        defect = f"no instant produced ({actual!r}) where the RFC fixes {expected_instant}"
        return CaseVerdict(case.case_id, case.criterion_id, False, (defect,))
    if actual.tzinfo is None or actual.utcoffset() is None:
        defect = f"naive datetime produced where the RFC fixes the instant {expected_instant}"
        return CaseVerdict(case.case_id, case.criterion_id, False, (defect,))
    if actual != expected:
        defect = f"instant differs from the RFC instant {expected_instant} by {actual - expected}"
        return CaseVerdict(case.case_id, case.criterion_id, False, (defect,))
    return CaseVerdict(case.case_id, case.criterion_id, True, ())


def _measured_actual(case: TimeCase) -> datetime | str | None:
    """Invoke the measured date parser for the ACTUAL result only (R12).

    Args:
        case: The fixture whose header value is handed to the component.

    Returns:
        The parser's datetime, or `malformed` — the only refusal the component can express, since
        it collapses "not a date" and "no usable zone" into a single `None`.
    """
    from app.connectors.imap.parsing.headers import parse_date

    parsed = parse_date(case.header_value)
    return MALFORMED if parsed is None else parsed


def _decision_text(criterion: Criterion, numerator: int, denominator: int) -> str:
    """Render the measured ratio and the annex rule it was decided against (prose, not a rule)."""
    return f"{numerator}/{denominator} {criterion.operator} {criterion.threshold}"


def _score_fixtures(criterion: Criterion) -> tuple[dict[str, object], tuple[CaseVerdict, ...]]:
    """Route every TIME fixture through `score_case` and build its §3.4 entry."""
    verdicts: list[CaseVerdict] = []
    errors = 0
    for case in TIME_CASES:
        try:
            verdicts.append(score_case(case, _measured_actual(case)))
        except Exception as exc:  # noqa: BLE001 - R2: a scoring failure never shrinks the denominator
            errors += 1
            verdicts.append(
                CaseVerdict(
                    case.case_id, case.criterion_id, False, (f"raised {type(exc).__name__}",)
                )
            )
    denominator = len(TIME_CASES)
    numerator = sum(1 for verdict in verdicts if not verdict.passed)
    decision = _decision_text(criterion, numerator, denominator)
    entry = criterion_entry(
        criterion,
        reason=f"{decision}; fixtures whose (value, instant-or-state, provenance) tuple is wrong",
        numerator=numerator,
        denominator=denominator,
        expected=denominator,
        evaluated=denominator - errors,
        errors=errors,
    )
    return entry, tuple(verdicts)


def reference_instant(header_value: str) -> datetime | str:
    """The RFC reading of one stored `Date` header, computed independently of the component.

    Args:
        header_value: The retained `Date` header value.

    Returns:
        A timezone-aware UTC-comparable `datetime` when the header carries a zone that fixes an
        offset (a numeric zone other than `-0000`, or one of the RFC 5322 4.3 obs-zone names),
        `unknown_zone` when it does not, and `malformed` when it is not a date-time at all.
    """
    stripped = header_value.strip()
    numeric = _NUMERIC_ZONE.search(stripped)
    alphabetic = None if numeric else _ALPHABETIC_ZONE.search(stripped)
    known = bool(numeric and numeric.group(1) != "-0000") or bool(
        alphabetic and alphabetic.group(1).upper() in _OBSOLETE_ZONE_OFFSETS
    )
    try:
        parsed = parsedate_to_datetime(stripped)
    except (TypeError, ValueError):
        return MALFORMED
    if not known or parsed.tzinfo is None or parsed.utcoffset() is None:
        return UNKNOWN_ZONE
    return parsed


def _compare_rows(rows: Sequence[object]) -> tuple[int, dict[str, int]]:
    """Count the stored rows whose `(instant, provenance)` tuple contradicts their `Date` header.

    Args:
        rows: The corpus rows, each carrying `date_header` and `sent_at`.

    Returns:
        The inconsistent-row count and the aggregate breakdown by cause.
    """
    tally = {
        "undisclosed_assumption": 0,
        "malformed_header": 0,
        "wrong_instant": 0,
        "missing_stored_instant": 0,
    }
    for row in rows:
        stored = row.sent_at  # type: ignore[attr-defined]  # SQLAlchemy Row, typed loosely
        reference = reference_instant(row.date_header)  # type: ignore[attr-defined]
        if isinstance(reference, str):
            if stored is not None:
                key = "malformed_header" if reference == MALFORMED else "undisclosed_assumption"
                tally[key] += 1
        elif stored is None:
            tally["missing_stored_instant"] += 1
        elif stored != reference:
            tally["wrong_instant"] += 1
    return sum(tally.values()), tally


async def _score_comparison(
    ctx: GateContext, criterion: Criterion
) -> tuple[dict[str, object], dict[str, object]]:
    """Compare stored `sent_at` with the retained `Date` header over the org's rows (R6)."""
    if ctx.corpus_snapshot is None:
        return criterion_entry(criterion, status=ERROR, reason=_NO_CORPUS_REASON), {
            "corpus_opened": False
        }
    async with ctx.corpus_snapshot() as session:
        rows = (await session.execute(_DATE_HEADER_SQL, {"org_id": ctx.org_id})).all()
    numerator, tally = _compare_rows(rows)
    status = criterion_status(criterion, numerator=numerator, denominator=len(rows))
    decision = _decision_text(criterion, numerator, len(rows))
    entry = criterion_entry(
        criterion,
        status=status,
        reason=f"{decision}; rows whose stored instant contradicts the retained Date header",
        numerator=numerator,
        denominator=len(rows),
        expected=len(rows),
        evaluated=len(rows),
    )
    return entry, {"corpus_opened": True, "rows_with_date_header": len(rows), **tally}


async def evaluate(ctx: GateContext) -> GateResult:
    """Return the TIME gate result: the fixture battery and the corpus header comparison.

    Args:
        ctx: The run context.

    Returns:
        A `GateResult` carrying both §3.4 entries and the aggregate diagnostics; FAIL on the
        Stage A corpus because the parser launders zone-less headers into UTC.
    """
    by_id = {criterion.id: criterion for criterion in ctx.criteria.by_gate.get(GATE, ())}
    fixture_entry, verdicts = _score_fixtures(by_id[FIXTURES_CRITERION_ID])
    comparison_entry, diagnostics = await _score_comparison(ctx, by_id[COMPARISON_CRITERION_ID])
    diagnostics["fixture_cases"] = len(TIME_CASES)
    built = {str(entry["id"]): entry for entry in (fixture_entry, comparison_entry)}
    entries = [built[criterion.id] for criterion in ctx.criteria.by_gate.get(GATE, ())]
    status = derive_gate_status(entries)
    report = write_gate_report(
        ctx.report_dir,
        name=GATE,
        status=status,
        reason=_GATE_REASON,
        criteria=entries,
        cases=verdicts,
        diagnostics=diagnostics,
    )
    return GateResult(
        name=GATE,
        status=status,
        reason=_GATE_REASON,
        criteria=tuple(entries),
        diagnostics=diagnostics,
        report_files=(report,),
    )
