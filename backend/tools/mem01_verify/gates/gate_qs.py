"""
Role: The QS gate (quote stripping) of contract §11 — the quote stripper and the labeled H split
      do not exist in Stage A, so every QS criterion is `incomplete` and the gate is `incomplete`;
      what the gate CAN report is the §11 diagnostic: how many of the tenant's emails carry a
      quote marker at all, counted on the R6 corpus snapshot.
Used by: `tools.mem01_verify.gates.registry` (`evaluate`) and the runner's hidden-scorability
      check (`hidden_scorable`, §16.13); sealed by `tests/tools/mem01_verify/test_gates_stage_a.py`
      and `test_gates_registry.py`.
Depends on: `tools.mem01_verify.gates.context` (context, result and entry writers), `.statuses`
      (the status algebra) and, at call time, the corpus snapshot factory the context carries.
Key invariants:
  - R3: no QS criterion may print PASS while the stripper is absent — the entries are `incomplete`
    by construction, never derived from a measurement.
  - The diagnostic counts EMAILS, never their text: no subject, body or address reaches the block
    (R5). It never affects the gate status (§3.3).
  - `hidden_scorable()` is False for the whole of Stage A: no measured component, no labels, so a
    `--checkpoint` run must not reserve a QS hidden unit (§16.13).
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import text

from tools.mem01_verify.gates.context import (
    GateContext,
    GateResult,
    incomplete_entries,
    write_gate_report,
)
from tools.mem01_verify.statuses import derive_gate_status

GATE = "QS"
#: §12: this gate never touches `ctx.probe`, so it alone never leases a probe.
NEEDS_PROBE: bool = False
_GATE_REASON = (
    "quote stripper absent and the QS H split carries no labels: no criterion is measurable "
    "in stage A"
)
_CRITERION_REASON = "no quote-stripping component and no labeled QS evidence exists yet (stage C)"

#: Quote markers §11 names: a line opening with '>', and the attribution lines of both languages.
_QUOTE_MARKER_SQL = text(
    "SELECT "
    " count(*) FILTER (WHERE strpos(body_text, chr(10) || '>') > 0 OR body_text LIKE '>%')"
    "   AS quoted_line_emails,"
    " count(*) FILTER (WHERE body_text ILIKE '%wrote:%' OR body_text ILIKE '%написа:%')"
    "   AS attribution_line_emails,"
    " count(*) FILTER (WHERE strpos(body_text, chr(10) || '>') > 0 OR body_text LIKE '>%'"
    "                     OR body_text ILIKE '%wrote:%' OR body_text ILIKE '%написа:%')"
    "   AS quote_marker_emails,"
    " count(*) AS emails_with_body "
    "FROM email_message WHERE org_id = :org_id AND body_text IS NOT NULL"
)


def hidden_scorable() -> bool:
    """False in stage A: QS has no measured component and no labeled hidden evidence (§16.13)."""
    return False


async def _count_quote_markers(ctx: GateContext) -> Mapping[str, object]:
    """Count the tenant's quote-marker-bearing emails on one R6 snapshot.

    Args:
        ctx: The run context; its `corpus_snapshot` factory opens the read-only snapshot.

    Returns:
        The four aggregate counts, or `N/A` values when this run never opened the corpus.
    """
    if ctx.corpus_snapshot is None:
        return {"quote_marker_emails": "N/A", "corpus_opened": False}
    async with ctx.corpus_snapshot() as session:
        row = (await session.execute(_QUOTE_MARKER_SQL, {"org_id": ctx.org_id})).one()
    return {
        "corpus_opened": True,
        "emails_with_body": int(row.emails_with_body),
        "quote_marker_emails": int(row.quote_marker_emails),
        "quoted_line_emails": int(row.quoted_line_emails),
        "attribution_line_emails": int(row.attribution_line_emails),
    }


async def evaluate(ctx: GateContext) -> GateResult:
    """Return the QS gate result: `incomplete`, with the §11 quote-marker diagnostic.

    Args:
        ctx: The run context.

    Returns:
        A `GateResult` whose every criterion is `incomplete` (R3) and whose diagnostics carry
        the quote-marker aggregates and the stage-A scorability flag.
    """
    entries = incomplete_entries(ctx.criteria.by_gate.get(GATE, ()), _CRITERION_REASON)
    diagnostics = {**await _count_quote_markers(ctx), "hidden_scorable": hidden_scorable()}
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
        criteria=entries,
        diagnostics=diagnostics,
        report_files=(report,),
    )
