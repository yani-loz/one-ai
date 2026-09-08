"""
Role: The COV gate (input coverage) of contract §11 — the frozen scope policy of §4.6 turned into
      one disposition function that both the F battery (`fixtures.cov_scenarios`) and every
      physical corpus input pass through, plus the three §3.4 criteria entries it decides and the
      explicitly-excluded list the machine block prints.
Used by: `tools.mem01_verify.gates.registry` (`evaluate`); the pure surfaces `dispose` and
      `score_scenario` are sealed by `tests/tools/mem01_verify/test_gate_scoring.py`, the gate
      outcome by `test_gates_stage_a.py`.
Depends on: `tools.mem01_verify.gates.context` (context, result, `CaseVerdict`, the §3.4 entry
      writers), `.criteria` (the `Criterion` record), `.statuses` (the status algebra),
      `.fixtures.cov_scenarios` (the F battery — data only) and, at call time, the corpus
      snapshot factory the context carries.
Key invariants:
  - §4.6: an exclusion comes ONLY from an independently established property (the declared MIME
    class, or `is_inline` on an image MIME). A processing status can neither create an exclusion
    nor establish delivery, and the MIME-class clause is named first when several match.
  - §16.17(c): a partial extraction is not a delivered one. The `partial_marker_absent` clause
    of `delivered_requires.attachment` refuses any input whose structured payload stored the
    truncation marker (`extracted_data->>'truncated' = 'true'`), text and full provenance
    notwithstanding. Such a row is `not_ready` and carries NO reason — §16.9 reserves `reason`
    for exclusions — so the rows the marker refused are counted in `diagnostics`
    (`not_ready_partial_marker`, free-form, never affecting status) instead.
  - Delivery comes ONLY from `delivered_requires`, keyed by input kind; every required input that
    is neither excluded nor delivered is `not_ready` — a duplicate carries the disposition its own
    properties give it.
  - Every scored input — fixture record AND corpus row — is routed through `dispose`, so the
    corpus and the battery can never diverge on the policy (§16.11 wiring rule).
  - The exclusions list carries ids, policy codes and the policy reference only: no filename, no
    subject, no address ever reaches the block (R5).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import text

from tools.mem01_verify.criteria import Criterion
from tools.mem01_verify.fixtures.cov_scenarios import COV_SCENARIOS, CovScenario, Disposition
from tools.mem01_verify.gates.context import (
    CaseVerdict,
    GateContext,
    GateResult,
    criterion_entry,
    criterion_status,
    write_gate_report,
)
from tools.mem01_verify.statuses import ERROR, derive_gate_status

GATE = "COV"
#: §12: this gate never touches `ctx.probe`, so it alone never leases a probe.
NEEDS_PROBE: bool = False
ACCOUNTED_CRITERION_ID = "cov.physical_inputs_accounted"
DELIVERED_CRITERION_ID = "cov.required_logical_delivered"
FIXTURES_CRITERION_ID = "cov.fixtures"

DELIVERED: Disposition = "delivered"
EXCLUDED: Disposition = "explicitly_excluded"
NOT_READY: Disposition = "not_ready"

_GATE_REASON = (
    "coverage is measured in full: every physical input is accounted for, and the required "
    "logical items that carry no complete declared handoff make the delivery criterion FAIL"
)
_NO_CORPUS_REASON = "this run never opened the corpus, so coverage could not be measured (R2)"
_INLINE_IMAGE_REASON = "excluded_inline_image"
#: The §16.17(c) diagnostics key counting the corpus rows the partial marker refused.
_PARTIAL_MARKER_COUNT_KEY = "not_ready_partial_marker"

_EMAIL_SQL = text(
    "SELECT id, parse_status, (body_text IS NOT NULL) AS text_present "
    "FROM email_message WHERE org_id = :org_id ORDER BY id"
)
_ATTACHMENT_SQL = text(
    "SELECT id, content_type, is_inline, extraction_status,"
    " (extracted_text IS NOT NULL AND extracted_text <> '') AS text_present,"
    " (extractor_name IS NOT NULL) AS extractor_name_present,"
    " (extractor_version IS NOT NULL) AS extractor_version_present,"
    " (extracted_data->>'truncated' = 'true') AS structured_truncated "
    "FROM email_attachment WHERE org_id = :org_id ORDER BY id"
)


class ScopedInput(Protocol):
    """The property set the frozen scope policy disposes on — nothing else is consulted.

    Contract:
        `kind` is `email` or `attachment`; `extraction_status` carries the input's own declared
        processing status (an email's `parse_status` for `kind == "email"`); `text_present` means
        a stored body for an email and stored extracted text for an attachment;
        `structured_truncated` is the stored partial marker of a structured extraction
        (§16.17(c)) and is False for every input that carries none. Both fixture records
        (`CovScenario`) and corpus rows (`CorpusInput`) satisfy this shape.
    """

    kind: str
    content_type: str | None
    is_inline: bool
    extraction_status: str
    text_present: bool
    extractor_name_present: bool
    extractor_version_present: bool
    structured_truncated: bool


@dataclass(frozen=True, slots=True)
class CorpusInput:
    """One physical corpus input described by the same properties a fixture record carries."""

    input_id: str
    kind: str
    content_type: str | None
    is_inline: bool
    extraction_status: str
    text_present: bool
    extractor_name_present: bool
    extractor_version_present: bool
    structured_truncated: bool


def _normalized_content_type(content_type: str | None) -> str:
    """Lowercase the declared MIME type and drop its parameters (`text/plain; charset=…`)."""
    if not content_type:
        return ""
    return content_type.split(";", 1)[0].strip().lower()


def _exclusion_reason(scoped: ScopedInput, policy: Mapping[str, object]) -> str | None:
    """The policy code excluding this input by property, or None — MIME clauses first (§4.6).

    Returns `excluded_content_type_prefix:<prefix>`, `excluded_content_type_exact:<type>` or
    `excluded_inline_image`, in the clause order §4.6 fixes, from the frozen `scope_policy`.
    """
    excluded = policy.get("excluded_by_property")
    if not isinstance(excluded, Mapping):
        return None
    declared = _normalized_content_type(scoped.content_type)
    prefixes = excluded.get("content_type_prefixes")
    if isinstance(prefixes, Sequence) and not isinstance(prefixes, str | bytes):
        for prefix in prefixes:
            if isinstance(prefix, str) and declared.startswith(prefix.lower()):
                return f"excluded_content_type_prefix:{prefix}"
    exact = excluded.get("content_type_exact")
    if isinstance(exact, Sequence) and not isinstance(exact, str | bytes):
        for name in exact:
            if isinstance(name, str) and declared == name.lower():
                return f"excluded_content_type_exact:{name}"
    if excluded.get("inline_image") and scoped.is_inline and declared.startswith("image/"):
        return _INLINE_IMAGE_REASON
    return None


def _is_delivered(scoped: ScopedInput, policy: Mapping[str, object]) -> bool:
    """True iff the declared handoff for this input kind is complete (§4.6 `delivered_requires`).

    Email: `parse_status == parsed` and a stored body. Attachment: `extraction_status ==
    extracted`, stored text, and both extractor fields recorded. A status alone never delivers.
    A clause carrying `partial_marker_absent` additionally refuses an input whose structured
    extraction stored the partial marker: a truncated extraction is not a delivered one
    (§16.17(c)). A policy without that key keeps the earlier behaviour.
    """
    requires = policy.get("delivered_requires")
    clause = requires.get(scoped.kind) if isinstance(requires, Mapping) else None
    if not isinstance(clause, Mapping):
        return False
    if clause.get("partial_marker_absent") and scoped.structured_truncated:
        return False
    if scoped.kind == "email":
        return scoped.extraction_status == clause.get("parse_status") and scoped.text_present
    return (
        scoped.extraction_status == clause.get("extraction_status")
        and scoped.text_present
        and scoped.extractor_name_present
        and scoped.extractor_version_present
    )


def dispose_with_reason(
    scoped: ScopedInput, policy: Mapping[str, object]
) -> tuple[Disposition, str | None]:
    """Dispose one input under the frozen scope policy and name the clause it turned on.

    Returns the disposition and, for `explicitly_excluded` ALONE, the single canonical policy
    reason code (§16.9: one reason when several clauses match). `delivered` and `not_ready`
    both carry None — §16.9 reserves `reason` for exclusions, and a row the partial marker
    refused is counted in the COV diagnostics rather than given a reason code of its own
    (§16.17(c)).
    """
    reason = _exclusion_reason(scoped, policy)
    if reason is not None:
        return EXCLUDED, reason
    if _is_delivered(scoped, policy):
        return DELIVERED, None
    return NOT_READY, None


def dispose(scenario: ScopedInput, policy: Mapping[str, object]) -> Disposition:
    """The disposition the frozen scope policy gives one input (§16.11 pure surface).

    `scenario` is a `CovScenario` fixture record or a `CorpusInput` corpus row; the result is
    `delivered`, `explicitly_excluded` or `not_ready`.
    """
    return dispose_with_reason(scenario, policy)[0]


def score_scenario(scenario: CovScenario, disposition: Disposition) -> CaseVerdict:
    """Score one coverage scenario: the disposition must equal the fixture's own expectation.

    Passes iff `disposition` equals `scenario.expected.disposition` (R12: the expected side is
    the fixture record, never a measured component); the single defect names both values.
    """
    expected = scenario.expected.disposition
    if disposition == expected:
        return CaseVerdict(scenario.case_id, scenario.criterion_id, True, ())
    defect = f"disposition {disposition!r} where the frozen policy requires {expected!r}"
    return CaseVerdict(scenario.case_id, scenario.criterion_id, False, (defect,))


def _decision_text(criterion: Criterion, numerator: int, denominator: int) -> str:
    """Render the measured ratio and the annex rule it was decided against (prose, not a rule)."""
    return f"{numerator}/{denominator} {criterion.operator} {criterion.threshold}"


def _score_fixtures(
    criterion: Criterion, policy: Mapping[str, object]
) -> tuple[dict[str, object], tuple[CaseVerdict, ...]]:
    """Route every COV fixture through `dispose` + `score_scenario` and build its §3.4 entry."""
    verdicts: list[CaseVerdict] = []
    errors = 0
    for scenario in COV_SCENARIOS:
        try:
            verdicts.append(score_scenario(scenario, dispose(scenario, policy)))
        except Exception as exc:  # noqa: BLE001 - R2: a scoring failure never shrinks the denominator
            errors += 1
            verdicts.append(
                CaseVerdict(
                    scenario.case_id,
                    scenario.criterion_id,
                    False,
                    (f"scoring raised {type(exc).__name__}",),
                )
            )
    denominator = len(COV_SCENARIOS)
    numerator = sum(1 for verdict in verdicts if not verdict.passed)
    decision = _decision_text(criterion, numerator, denominator)
    entry = criterion_entry(
        criterion,
        reason=f"{decision}; scenarios with a wrong disposition under scope_policy v0",
        numerator=numerator,
        denominator=denominator,
        expected=denominator,
        evaluated=denominator - errors,
        errors=errors,
    )
    return entry, tuple(verdicts)


async def _read_corpus_inputs(ctx: GateContext) -> list[CorpusInput]:
    """Read every physical input of the org — emails then attachments — from the R6 snapshot."""
    async with ctx.corpus_snapshot() as session:  # type: ignore[misc]  # None handled by caller
        emails = (await session.execute(_EMAIL_SQL, {"org_id": ctx.org_id})).all()
        attachments = (await session.execute(_ATTACHMENT_SQL, {"org_id": ctx.org_id})).all()
    inputs = [
        CorpusInput(
            input_id=str(row.id),
            kind="email",
            content_type="message/rfc822",
            is_inline=False,
            extraction_status=row.parse_status,
            text_present=bool(row.text_present),
            extractor_name_present=False,
            extractor_version_present=False,
            structured_truncated=False,
        )
        for row in emails
    ]
    inputs.extend(
        CorpusInput(
            input_id=str(row.id),
            kind="attachment",
            content_type=row.content_type,
            is_inline=bool(row.is_inline),
            extraction_status=row.extraction_status,
            text_present=bool(row.text_present),
            extractor_name_present=bool(row.extractor_name_present),
            extractor_version_present=bool(row.extractor_version_present),
            structured_truncated=bool(row.structured_truncated),
        )
        for row in attachments
    )
    return inputs


def _policy_reference(policy: Mapping[str, object]) -> str:
    """The `policy_ref` every exclusion row carries: the policy name, its version and the clause."""
    version = policy.get("version")
    return f"scope_policy:{version if isinstance(version, str) else 'unknown'}:excluded_by_property"


def _corpus_entries(
    accounted: Criterion,
    delivered: Criterion,
    inputs: Sequence[CorpusInput],
    policy: Mapping[str, object],
) -> tuple[list[dict[str, object]], list[dict[str, str]], dict[str, int]]:
    """Dispose every corpus input and build the two C entries, the exclusions and the counts.

    The counts carry the §16.17(c) diagnostic alongside the dispositions: how many `not_ready`
    rows stored the partial marker. It is evidence for the reader, never a status input, and
    never a reason code on the row itself.
    """
    tally = {DELIVERED: 0, EXCLUDED: 0, NOT_READY: 0}
    exclusions: list[dict[str, str]] = []
    partial_marker_refused = 0
    policy_ref = _policy_reference(policy)
    for scoped in inputs:
        disposition, reason = dispose_with_reason(scoped, policy)
        tally[disposition] += 1
        if disposition == NOT_READY and scoped.structured_truncated:
            partial_marker_refused += 1
        if disposition == EXCLUDED and reason is not None:
            exclusions.append({"id": scoped.input_id, "reason": reason, "policy_ref": policy_ref})
    physical = len(inputs)
    unaccounted = physical - sum(tally.values())
    accounted_status = criterion_status(accounted, numerator=unaccounted, denominator=physical)
    accounted_decision = _decision_text(accounted, unaccounted, physical)
    required = physical - tally[EXCLUDED]
    delivered_status = criterion_status(delivered, numerator=tally[NOT_READY], denominator=required)
    delivered_decision = _decision_text(delivered, tally[NOT_READY], required)
    entries = [
        criterion_entry(
            accounted,
            status=accounted_status,
            reason=f"{accounted_decision}; every physical input carries a disposition",
            numerator=unaccounted,
            denominator=physical,
            expected=physical,
            evaluated=physical,
        ),
        criterion_entry(
            delivered,
            status=delivered_status,
            reason=f"{delivered_decision}; required logical items without a complete handoff",
            numerator=tally[NOT_READY],
            denominator=required,
            expected=required,
            evaluated=required,
        ),
    ]
    counts = {
        "physical_inputs": physical,
        "delivered": tally[DELIVERED],
        "explicitly_excluded": tally[EXCLUDED],
        "not_ready": tally[NOT_READY],
        "required_logical_items": required,
        _PARTIAL_MARKER_COUNT_KEY: partial_marker_refused,
    }
    return entries, exclusions, counts


async def evaluate(ctx: GateContext) -> GateResult:
    """Return the COV gate result: the scope policy applied to the corpus and to the F battery.

    Args:
        ctx: The run context.

    Returns:
        A `GateResult` carrying the three §3.4 entries, the aggregate dispositions, and the
        `{id, reason, policy_ref}` rows the runner lifts into the block's `exclusions` list —
        ids and policy codes only, never a filename, a subject or an address (R5).
    """
    policy = ctx.criteria.scope_policy
    by_id = {criterion.id: criterion for criterion in ctx.criteria.by_gate.get(GATE, ())}
    fixture_entry, verdicts = _score_fixtures(by_id[FIXTURES_CRITERION_ID], policy)
    diagnostics: dict[str, object] = {"fixture_scenarios": len(COV_SCENARIOS)}
    if ctx.corpus_snapshot is None:
        corpus_entries = [
            criterion_entry(by_id[name], status=ERROR, reason=_NO_CORPUS_REASON)
            for name in (ACCOUNTED_CRITERION_ID, DELIVERED_CRITERION_ID)
        ]
        exclusions: list[dict[str, str]] = []
        diagnostics["corpus_opened"] = False
    else:
        corpus_entries, exclusions, counts = _corpus_entries(
            by_id[ACCOUNTED_CRITERION_ID],
            by_id[DELIVERED_CRITERION_ID],
            await _read_corpus_inputs(ctx),
            policy,
        )
        diagnostics.update({"corpus_opened": True, **counts})
    built = {str(entry["id"]): entry for entry in (*corpus_entries, fixture_entry)}
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
        exclusions=tuple(exclusions),
    )
