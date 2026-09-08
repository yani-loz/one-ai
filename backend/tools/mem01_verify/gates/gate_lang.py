"""
Role: The LANG gate (language labelling) of contract §11 — the language detector and the labeled
      LANG H split do not exist in Stage A, so the two accuracy criteria are `incomplete`, while
      the corpus invariant `lang.no_invalid_states` IS measurable over the R6 snapshot and FAILS
      visibly at 100% NULL. The gate itself is `incomplete` (§3.5: incomplete outranks FAIL).
Used by: `tools.mem01_verify.gates.registry` (`evaluate`) and the runner's hidden-scorability
      check (`hidden_scorable`, §16.13); sealed by `tests/tools/mem01_verify/test_gates_stage_a.py`
      and `test_gates_registry.py`.
Depends on: `tools.mem01_verify.gates.context` (context, result and the §3.4 entry writers),
      `.criteria` (the `Criterion` record), `.statuses` (the status algebra) and, at call time,
      the corpus snapshot factory the context carries.
Key invariants:
  - R3: `lang.accuracy_overall` / `lang.accuracy_per_class` never print PASS while no detector and
    no labels exist — they are `incomplete` by construction, never derived from a measurement.
  - The corpus denominator is the §5.1 language-bearing item set: every email of the org plus
    every attachment carrying non-null `extracted_text`. `email_attachment` has NO language
    column, so every text-bearing attachment is an invalid state by construction.
  - Counts only reach the block; no subject, body, address or filename does (R5).
  - `hidden_scorable()` is False for the whole of Stage A (§16.13).
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import text

from tools.mem01_verify.criteria import Criterion
from tools.mem01_verify.gates.context import (
    GateContext,
    GateResult,
    criterion_entry,
    criterion_status,
    incomplete_entries,
    write_gate_report,
)
from tools.mem01_verify.statuses import ERROR, derive_gate_status

GATE = "LANG"
#: §12: this gate never touches `ctx.probe`, so it alone never leases a probe.
NEEDS_PROBE: bool = False
CORPUS_CRITERION_ID = "lang.no_invalid_states"

#: The only language values the criterion accepts on an email (the annex formula).
VALID_LANGUAGE_VALUES: tuple[str, ...] = ("bg", "en", "mixed", "und")

_GATE_REASON = (
    "language detector absent and the LANG H split carries no labels: the accuracy criteria are "
    "incomplete; the corpus invariant is measured and its result stays visible in the entries"
)
_H_CRITERION_REASON = "no language detector and no labeled LANG evidence exists yet (stage C)"
_NO_CORPUS_REASON = "this run never opened the corpus, so the invariant could not be measured (R2)"
_ATTACHMENT_REASON = "email_attachment carries no language column: every text-bearing attachment "
_UND_CAVEAT = "the 'und on an assessable item' clause needs labels and is not evaluable in stage A"

_VALID_LANGUAGE_LITERAL = ", ".join(f"'{value}'" for value in VALID_LANGUAGE_VALUES)
_EMAIL_LANGUAGE_SQL = text(
    "SELECT count(*) AS emails,"
    " count(*) FILTER (WHERE language IS NULL) AS language_null,"
    f" count(*) FILTER (WHERE language IS NOT NULL AND language NOT IN ({_VALID_LANGUAGE_LITERAL}))"
    "   AS language_unexpected "
    "FROM email_message WHERE org_id = :org_id"
)
_ATTACHMENT_TEXT_SQL = text(
    "SELECT count(*) AS text_attachments "
    "FROM email_attachment WHERE org_id = :org_id AND extracted_text IS NOT NULL"
)


def hidden_scorable() -> bool:
    """False in stage A: LANG has no detector and no labeled hidden evidence (§16.13)."""
    return False


def _decision_text(criterion: Criterion, numerator: int, denominator: int) -> str:
    """Render the measured ratio and the annex rule it was decided against (prose, not a rule)."""
    return f"{numerator}/{denominator} {criterion.operator} {criterion.threshold}"


async def _count_language_states(ctx: GateContext) -> dict[str, int]:
    """Count the org's emails by language state and its text-bearing attachments (R6 snapshot).

    Args:
        ctx: The run context; its `corpus_snapshot` factory opens the read-only snapshot.

    Returns:
        `emails`, `language_null`, `language_unexpected` and `text_attachments` as integers.
    """
    async with ctx.corpus_snapshot() as session:  # type: ignore[misc]  # None handled by caller
        emails = (await session.execute(_EMAIL_LANGUAGE_SQL, {"org_id": ctx.org_id})).one()
        attachments = (await session.execute(_ATTACHMENT_TEXT_SQL, {"org_id": ctx.org_id})).one()
    return {
        "emails": int(emails.emails),
        "language_null": int(emails.language_null),
        "language_unexpected": int(emails.language_unexpected),
        "text_attachments": int(attachments.text_attachments),
    }


def _corpus_entry(
    criterion: Criterion, counts: Mapping[str, int] | None
) -> tuple[dict[str, object], dict[str, object]]:
    """Build the `lang.no_invalid_states` entry and its diagnostics from the measured counts.

    Args:
        criterion: The annex record for `lang.no_invalid_states`.
        counts: The measured counts, or None when the corpus was never opened.

    Returns:
        The §3.4 entry and the aggregate diagnostics that accompany it.
    """
    if counts is None:
        return criterion_entry(criterion, status=ERROR, reason=_NO_CORPUS_REASON), {
            "corpus_opened": False
        }
    denominator = counts["emails"] + counts["text_attachments"]
    numerator = counts["language_null"] + counts["language_unexpected"] + counts["text_attachments"]
    status = criterion_status(criterion, numerator=numerator, denominator=denominator)
    decision = _decision_text(criterion, numerator, denominator)
    reason = (
        f"{decision}; {_ATTACHMENT_REASON}is an invalid language state by construction; "
        f"{_UND_CAVEAT}"
    )
    entry = criterion_entry(
        criterion,
        status=status,
        reason=reason,
        numerator=numerator,
        denominator=denominator,
        expected=denominator,
        evaluated=denominator,
    )
    return entry, {"corpus_opened": True, **dict(counts), "invalid_items": numerator}


async def evaluate(ctx: GateContext) -> GateResult:
    """Return the LANG gate result: `incomplete`, with the corpus invariant measured and visible.

    Args:
        ctx: The run context.

    Returns:
        A `GateResult` whose H criteria are `incomplete` (R3) and whose C criterion carries the
        measured NULL-language invariant — FAIL on the Stage A corpus, never suppressed.
    """
    gate_criteria = ctx.criteria.by_gate.get(GATE, ())
    counts = await _count_language_states(ctx) if ctx.corpus_snapshot is not None else None
    entries: list[dict[str, object]] = []
    diagnostics: dict[str, object] = {"hidden_scorable": hidden_scorable()}
    for criterion in gate_criteria:
        if criterion.id == CORPUS_CRITERION_ID:
            entry, measured = _corpus_entry(criterion, counts)
            diagnostics.update(measured)
        else:
            entry = incomplete_entries((criterion,), _H_CRITERION_REASON)[0]
        entries.append(entry)
    status = derive_gate_status(entries)
    report = write_gate_report(
        ctx.report_dir,
        name=GATE,
        status=status,
        reason=_GATE_REASON,
        criteria=entries,
        cases=(),
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
