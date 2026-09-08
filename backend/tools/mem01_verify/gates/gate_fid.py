"""
Role: The FID gate (extraction fidelity) of contract §11 — the only Stage-A gate decided in full
      on fixtures. It runs every `fixtures.fid_cases` ORIGINAL through the REAL extractor handoff
      (`attachment_extractor.extract_text`, whose `text` is byte-for-byte the value the ingest
      path stores in `email_attachment.extracted_text`) and scores the stored string against the
      independently authored expectation with the pure `score_case` of §16.11.
Used by: `tools.mem01_verify.gates.registry` (`evaluate`); sealed by
      `tests/tools/mem01_verify/test_gate_scoring.py` (the pure surface) and
      `test_gates_stage_a.py` (`fid.provisional` decided, `fid.validation` pending).
Depends on: `tools.mem01_verify.gates.context` (context, result, §3.4 entry writers),
      `.criteria`, `.statuses`, `.fixtures.fid_cases` (the battery — expectations only, R12) and
      the measured components `app.connectors.imap.parsing.models.ParsedAttachment` +
      `app.connectors.imap.parsing.attachment_extractor.extract_text` (ACTUAL results only).
Key invariants:
  - R12: the expected side is always the fixture record. `extract_text` is called to obtain an
    ACTUAL stored string and never to learn what should have survived.
  - `score_case` is PURE — same case and same stored text in, same `CaseVerdict` out — so the
    oracle can seal the scoring logic with hand-made actual results (§16.11).
  - The v1 representation rules of `fid_cases_a` are implemented literally (§16.15): a unit is
    its literal text or one of its frozen alternatives; order is GREEDY FORWARD matching, never
    first-occurrence comparison; a row group's span runs from its first cell's match to its last
    cell's, and no excluded cell's literal may fall inside it.
  - `evaluate` routes EVERY case through `score_case`; no case is decided anywhere else.
  - Case detail lives only in `gates/FID.json`; the block carries counts (R5). Fixture text is
    synthetic and PII-free, but the same discipline applies.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256

from tools.mem01_verify.fixtures.fid_cases import FidCase, build_fid_cases
from tools.mem01_verify.fixtures.fid_cases_a import FidUnit, LinkPair, NegationGuard, RowGroup
from tools.mem01_verify.gates.context import (
    CaseVerdict,
    GateContext,
    GateResult,
    criterion_entry,
    incomplete_entries,
    write_gate_report,
)
from tools.mem01_verify.statuses import derive_gate_status

GATE = "FID"
#: §12: this gate never touches `ctx.probe`, so it alone never leases a probe.
NEEDS_PROBE: bool = False
PROVISIONAL_CRITERION_ID = "fid.provisional"

#: Defects reported per case in `gates/FID.json` — a full list would balloon the report with no
#: extra signal; the count of failing cases is what the criterion is decided on.
MAX_DEFECTS_PER_CASE = 12

_GATE_REASON = (
    "extraction fidelity is decided in full on the public fixture battery; the holdout criterion "
    "waits for the founder validation run"
)
_VALIDATION_REASON = "holdout fidelity cases are scored only on the founder validation run"
_EXTRACTOR_NAME = "attachment_extractor.extract_text"


def _literals(unit: FidUnit) -> tuple[str, ...]:
    """Return the accepted renderings of one unit: its literal text plus its frozen alternatives."""
    return (unit.text, *unit.alternatives)


def _earliest(stored: str, literals: Sequence[str], start: int) -> tuple[int, int] | None:
    """Find the earliest occurrence of any accepted rendering at or after `start`.

    Args:
        stored: The stored extracted text being scored.
        literals: The accepted renderings of one unit.
        start: The first position the match may begin at (greedy forward matching, §16.15).

    Returns:
        The `(start, end)` scalar interval of the earliest match, or None when none occurs.
    """
    best: tuple[int, int] | None = None
    for literal in literals:
        if not literal:
            continue
        found = stored.find(literal, start)
        if found >= 0 and (best is None or found < best[0]):
            best = (found, found + len(literal))
    return best


def _check_units(stored: str, units: Sequence[FidUnit], defects: list[str]) -> dict[str, FidUnit]:
    """Record a defect for every required unit the stored text does not carry; index the units."""
    by_id: dict[str, FidUnit] = {}
    for unit in units:
        by_id[unit.unit_id] = unit
        if _earliest(stored, _literals(unit), 0) is None:
            defects.append(f"missing_unit:{unit.unit_id}")
    return by_id


def _match_sequence(
    stored: str, unit_ids: Sequence[str], by_id: Mapping[str, FidUnit]
) -> tuple[int, int] | None:
    """Greedily match a unit sequence left to right; return its whole span, or None if it breaks.

    Each unit is matched at the earliest position at or after the previous unit's match end
    (§16.15 representation rule 2a), never by comparing first occurrences.
    """
    position = 0
    span_start: int | None = None
    for unit_id in unit_ids:
        unit = by_id.get(unit_id)
        if unit is None:
            return None
        found = _earliest(stored, _literals(unit), position)
        if found is None:
            return None
        if span_start is None:
            span_start = found[0]
        position = found[1]
    return None if span_start is None else (span_start, position)


def _check_order(
    stored: str,
    sequences: Sequence[Sequence[str]],
    by_id: Mapping[str, FidUnit],
    defects: list[str],
) -> None:
    """Record a defect for every ordered sequence that cannot be matched left to right."""
    for index, sequence in enumerate(sequences):
        if _match_sequence(stored, sequence, by_id) is None:
            defects.append(f"order_broken:{index}")


def _check_rows(
    stored: str, groups: Sequence[RowGroup], by_id: Mapping[str, FidUnit], defects: list[str]
) -> None:
    """Record a defect for every table row that is broken or interleaved with another row."""
    for index, group in enumerate(groups):
        span = _match_sequence(stored, group.unit_ids, by_id)
        if span is None:
            defects.append(f"row_broken:{index}")
            continue
        for foreign_id in group.excluded_unit_ids:
            foreign = by_id.get(foreign_id)
            if foreign is None:
                continue
            found = _earliest(stored, _literals(foreign), span[0])
            if found is not None and found[0] < span[1]:
                defects.append(f"row_interleaved:{index}:{foreign_id}")
                break


def _check_links(stored: str, links: Sequence[LinkPair], defects: list[str]) -> None:
    """Record a defect for every link whose destination does not follow its label closely enough."""
    for link in links:
        position, resolved = 0, False
        while not resolved:
            label = stored.find(link.label, position)
            if label < 0:
                break
            label_end = label + len(link.label)
            destination = stored.find(link.destination, label_end)
            resolved = 0 <= destination <= label_end + link.max_gap_scalars
            position = label + 1
        if not resolved:
            defects.append(f"link_broken:{link.label[:40]}")


def _check_negations(stored: str, guards: Sequence[NegationGuard], defects: list[str]) -> None:
    """Record a defect for every negation stem that occurs without its required prefix."""
    for guard in guards:
        position = 0
        while True:
            found = stored.find(guard.stem, position)
            if found < 0:
                break
            prefix_start = found - len(guard.required_prefix)
            if prefix_start < 0 or stored[prefix_start:found] != guard.required_prefix:
                defects.append(f"lost_negation:{guard.required_prefix.strip()}")
                break
            position = found + 1


def score_case(case: FidCase, stored_text: str) -> CaseVerdict:
    """Score one fidelity fixture against the stored `extracted_text` (§16.11, pure).

    Args:
        case: The fixture record — the ORIGINAL and its independently authored expectation.
        stored_text: The value the ingest path stores for this ORIGINAL's attachment. An empty
            string stands for the honest NULL an extractor returns when it produced no text.

    Contract:
        Passes iff every required unit occurs (as its literal or one of its frozen alternatives),
        every ordered sequence matches by greedy forward assignment, every table row matches with
        no foreign cell inside its span, every link's destination follows its label within the
        declared gap, every negation keeps its prefix, and no forbidden token occurs.

    Returns:
        A `CaseVerdict` whose `defects` are non-empty exactly when the case failed. Defects are
        capped at `MAX_DEFECTS_PER_CASE`; the verdict's `passed` flag is never capped.

    Edge cases:
        An empty stored text fails with one `missing_unit` defect per required unit (a handoff
        that loses everything is exactly what this gate exists to measure, §10.5) — never ERROR.
    """
    expected = case.expected
    defects: list[str] = []
    by_id = _check_units(stored_text, expected.units, defects)
    _check_order(stored_text, expected.ordered_sequences, by_id, defects)
    _check_rows(stored_text, expected.row_groups, by_id, defects)
    _check_links(stored_text, expected.link_pairs, defects)
    _check_negations(stored_text, expected.negation_guards, defects)
    for token in expected.forbidden:
        if token and token in stored_text:
            defects.append(f"forbidden_present:{token[:20]}")
    return CaseVerdict(
        case_id=case.case_id,
        criterion_id=case.criterion_id,
        passed=not defects,
        defects=tuple(defects[:MAX_DEFECTS_PER_CASE]),
    )


def _stored_text_for(case: FidCase) -> str:
    """Run one ORIGINAL through the real extractor handoff and return the value ingest stores.

    `EmailIngestService._store_attachments` writes `ExtractionResult.text` into
    `email_attachment.extracted_text` verbatim, so this IS the stored string (§10.5); an honest
    NULL is scored as an empty string.
    """
    from app.connectors.imap.parsing.attachment_extractor import extract_text
    from app.connectors.imap.parsing.models import ParsedAttachment

    attachment = ParsedAttachment(
        filename=f"{case.case_id}.{case.format}",
        content_type=case.content_type,
        size_bytes=len(case.payload),
        content_hash=sha256(case.payload).hexdigest(),
        is_inline=False,
        content_id=None,
        payload=case.payload,
    )
    return extract_text(attachment).text or ""


def _diagnostics(cases: Sequence[FidCase], verdicts: Sequence[CaseVerdict]) -> dict[str, object]:
    """Aggregate per-format counts for the block — counts only, never fixture text (R5)."""
    failed_by_format: dict[str, int] = {}
    total_by_format: dict[str, int] = {}
    for case, verdict in zip(cases, verdicts, strict=True):
        total_by_format[case.format] = total_by_format.get(case.format, 0) + 1
        if not verdict.passed:
            failed_by_format[case.format] = failed_by_format.get(case.format, 0) + 1
    return {
        "cases_total": len(cases),
        "cases_failed": sum(1 for verdict in verdicts if not verdict.passed),
        "cases_by_format": dict(sorted(total_by_format.items())),
        "failed_by_format": dict(sorted(failed_by_format.items())),
    }


async def evaluate(ctx: GateContext) -> GateResult:
    """Return the FID gate result: `fid.provisional` decided on fixtures, `fid.validation` pending.

    Args:
        ctx: The run context (the battery is public F evidence, so no database is opened).

    Returns:
        A `GateResult` carrying one entry per FID criterion, the per-format aggregates and the
        `gates/FID.json` report holding every `CaseVerdict`.
    """
    cases = build_fid_cases()
    verdicts = [score_case(case, _stored_text_for(case)) for case in cases]
    failed = sum(1 for verdict in verdicts if not verdict.passed)
    versions = {key: ctx.versions[key] for key in ("pdfplumber", "openpyxl") if key in ctx.versions}
    entries: list[dict[str, object]] = []
    for criterion in ctx.criteria.by_gate.get(GATE, ()):
        if criterion.id != PROVISIONAL_CRITERION_ID:
            entries.append(incomplete_entries((criterion,), _VALIDATION_REASON)[0])
            continue
        entries.append(
            criterion_entry(
                criterion,
                reason=(
                    f"{failed}/{len(cases)} defective fixtures scored on the stored "
                    f"extracted_text ({_EXTRACTOR_NAME})"
                ),
                numerator=failed,
                denominator=len(cases),
                expected=len(cases),
                evaluated=len(cases),
                versions=versions,
            )
        )
    status = derive_gate_status(entries)
    diagnostics = _diagnostics(cases, verdicts)
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


__all__ = ["GATE", "MAX_DEFECTS_PER_CASE", "PROVISIONAL_CRITERION_ID", "evaluate", "score_case"]
