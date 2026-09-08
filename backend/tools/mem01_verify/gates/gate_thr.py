"""
Role: The THR gate (threading) of contract §11 — production threading does not exist in Stage A,
      so no thread membership exists to check and no must-join / must-not-join fixture battery has
      been authored: every criterion is `incomplete` (validation entries `pending`) and the gate is
      `incomplete`. What the gate CAN report is the header-derived membership evidence a future
      threader would build on, counted on the R6 corpus snapshot as diagnostics.
Used by: `tools.mem01_verify.gates.registry` (`evaluate`); sealed by
      `tests/tools/mem01_verify/test_gates_stage_a.py` and `test_gates_registry.py`.
Depends on: `tools.mem01_verify.gates.context` (context, result and the §3.4 entry writers),
      `.statuses` (the status algebra) and, at call time, the corpus snapshot factory the context
      carries.
Key invariants:
  - R3: no THR criterion prints PASS while the threader is absent. In particular
    `thr.provisional.tenant_provenance` is `incomplete`, NOT scored: with no threader there are
    zero thread memberships, and `0/0` on a mandatory ratio is ERROR under §3.4, never a pass —
    so the honest status is `incomplete` and the header-derived counts go to diagnostics.
  - The diagnostics count EMAILS and header tokens, never their values: no `Message-ID`,
    `In-Reply-To` or `References` string reaches the block (R5).
  - THR is a holdout gate: its `.validation` criteria stay `pending` outside the founder run.
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

GATE = "THR"
#: §12: this gate never touches `ctx.probe`, so it alone never leases a probe.
NEEDS_PROBE: bool = False
_GATE_REASON = (
    "production threading absent: no thread-membership relation exists, so neither the fixture "
    "criteria nor the corpus tenant/provenance invariant can be scored in stage A"
)
_CRITERION_REASON = (
    "no production threading component and no authored must-join / must-not-join battery exists "
    "yet (stage C); the corpus invariant has zero memberships to check, which is incomplete "
    "evidence, never a pass and never a vacuous zero denominator (R3, §3.4)"
)

#: Header-derived membership evidence: what a future threader would join on, counted only.
_MEMBERSHIP_SQL = text(
    "SELECT count(*) AS emails,"
    " count(*) FILTER (WHERE message_id IS NOT NULL) AS with_message_id,"
    " count(*) FILTER (WHERE in_reply_to IS NOT NULL) AS with_in_reply_to,"
    ' count(*) FILTER (WHERE "references" IS NOT NULL'
    '                     AND cardinality("references") > 0) AS with_references,'
    " count(*) FILTER (WHERE in_reply_to IS NOT NULL"
    '                    OR ("references" IS NOT NULL AND cardinality("references") > 0))'
    "   AS header_derived_memberships "
    "FROM email_message WHERE org_id = :org_id"
)


async def _count_membership_evidence(ctx: GateContext) -> Mapping[str, object]:
    """Count the header-derived threading evidence of the org on one R6 snapshot.

    Args:
        ctx: The run context; its `corpus_snapshot` factory opens the read-only snapshot.

    Returns:
        The aggregate counts, or a `corpus_opened: False` marker when the corpus was not opened.
    """
    if ctx.corpus_snapshot is None:
        return {"corpus_opened": False, "header_derived_memberships": "N/A"}
    async with ctx.corpus_snapshot() as session:
        row = (await session.execute(_MEMBERSHIP_SQL, {"org_id": ctx.org_id})).one()
    return {
        "corpus_opened": True,
        "emails": int(row.emails),
        "emails_with_message_id": int(row.with_message_id),
        "emails_with_in_reply_to": int(row.with_in_reply_to),
        "emails_with_references": int(row.with_references),
        "header_derived_memberships": int(row.header_derived_memberships),
        "production_thread_memberships": 0,
    }


async def evaluate(ctx: GateContext) -> GateResult:
    """Return the THR gate result: `incomplete`, with the header-derived membership diagnostic.

    Args:
        ctx: The run context.

    Returns:
        A `GateResult` whose every criterion is `incomplete` — `pending` for the two
        validation-split entries — and whose diagnostics carry the aggregate header evidence.
    """
    entries = incomplete_entries(ctx.criteria.by_gate.get(GATE, ()), _CRITERION_REASON)
    diagnostics = dict(await _count_membership_evidence(ctx))
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
