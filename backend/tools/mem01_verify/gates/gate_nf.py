"""
Role: The NF gate (noise filter) of contract §11 — no classifier and no labeled H split exist in
      Stage A, so every NF criterion is `incomplete` and the gate is `incomplete`; the gate does
      report the §11 diagnostic: how many attachment parts the ingest already skipped as
      non-documents, broken down by declared MIME type, counted on the R6 corpus snapshot.
Used by: `tools.mem01_verify.gates.registry` (`evaluate`) and the runner's hidden-scorability
      check (`hidden_scorable`, §16.13); sealed by `tests/tools/mem01_verify/test_gates_stage_a.py`
      and `test_gates_registry.py`.
Depends on: `tools.mem01_verify.gates.context` (context, result and entry writers), `.statuses`
      (the status algebra) and, at call time, the corpus snapshot factory the context carries.
Key invariants:
  - R3: no NF criterion prints PASS while the classifier is absent. `nf.dedup_groups_correct` is
    corpus-scannable in principle, but the annex sets its `minimum` at 20 audited groups; scoring
    it on a corpus with fewer would be ERROR (R2), never a smaller denominator, so stage A leaves
    it `incomplete` with the rest of the gate.
  - The diagnostic reports declared MIME types and counts only — never a filename (R5).
  - `hidden_scorable()` is False for the whole of Stage A (§16.13).
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

GATE = "NF"
#: §12: this gate never touches `ctx.probe`, so it alone never leases a probe.
NEEDS_PROBE: bool = False
_GATE_REASON = (
    "noise classifier absent and the NF H split carries no labels: no criterion is measurable "
    "in stage A"
)
_CRITERION_REASON = (
    "no noise-filter component and no labeled NF evidence exists yet (stage C); the dedup audit "
    "has fewer groups than the annex minimum, which would be ERROR rather than a decision"
)
_SKIPPED_STATUS = "skipped_nondocument"

_SKIPPED_BY_MIME_SQL = text(
    "SELECT coalesce(content_type, 'unknown') AS content_type, count(*) AS count "
    "FROM email_attachment "
    "WHERE org_id = :org_id AND extraction_status = :status "
    "GROUP BY 1 ORDER BY count(*) DESC, 1 ASC"
)


def hidden_scorable() -> bool:
    """False in stage A: NF has no measured component and no labeled hidden evidence (§16.13)."""
    return False


async def _count_skipped_nondocuments(ctx: GateContext) -> Mapping[str, object]:
    """Count `skipped_nondocument` attachment parts per declared MIME type on one R6 snapshot.

    Args:
        ctx: The run context; its `corpus_snapshot` factory opens the read-only snapshot.

    Returns:
        The total plus a count-descending, key-ascending list of `{content_type, count}` rows,
        or `N/A` values when this run never opened the corpus.
    """
    if ctx.corpus_snapshot is None:
        return {"skipped_nondocument_total": "N/A", "corpus_opened": False}
    async with ctx.corpus_snapshot() as session:
        rows = (
            await session.execute(
                _SKIPPED_BY_MIME_SQL, {"org_id": ctx.org_id, "status": _SKIPPED_STATUS}
            )
        ).all()
    by_mime = [{"content_type": row.content_type, "count": int(row.count)} for row in rows]
    return {
        "corpus_opened": True,
        "skipped_nondocument_total": sum(int(row.count) for row in rows),
        "skipped_nondocument_by_content_type": by_mime,
    }


async def evaluate(ctx: GateContext) -> GateResult:
    """Return the NF gate result: `incomplete`, with the §11 skipped-non-document diagnostic.

    Args:
        ctx: The run context.

    Returns:
        A `GateResult` whose every criterion is `incomplete` (R3) and whose diagnostics carry the
        per-MIME skip counts and the stage-A scorability flag.
    """
    entries = incomplete_entries(ctx.criteria.by_gate.get(GATE, ()), _CRITERION_REASON)
    diagnostics = {
        **await _count_skipped_nondocuments(ctx),
        "hidden_scorable": hidden_scorable(),
    }
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
