"""
Role: The EMB gate (embeddings) of contract §11 — no embedding clerk and no required chunks exist
      in Stage A, so no vector can be recomputed or audited: every EMB criterion is `incomplete`
      and the gate is `incomplete`.
Used by: `tools.mem01_verify.gates.registry`; sealed by
      `tests/tools/mem01_verify/test_gates_stage_a.py` and `test_gates_registry.py`.
Depends on: `tools.mem01_verify.gates.context` (context, result and entry writers) and
      `.statuses` (the status algebra). No database: EMB's denominator is required CHUNKS, which
      the absent chunker never produced.
Key invariants:
  - R3: no EMB criterion prints PASS while the clerk is absent; the egress and generative-call
    criteria have no monitored execution to observe, which §3.4 makes ERROR rather than a vacuous
    pass, so nothing here may be decided.
  - The diagnostics carry no measurement, only the reason the component is missing (§3.3).
"""

from __future__ import annotations

from tools.mem01_verify.gates.context import (
    GateContext,
    GateResult,
    incomplete_entries,
    write_gate_report,
)
from tools.mem01_verify.statuses import derive_gate_status

GATE = "EMB"
#: §12: this gate never touches `ctx.probe`, so it alone never leases a probe.
NEEDS_PROBE: bool = False
_GATE_REASON = (
    "embedding clerk absent and no required chunk exists: no vector can be recomputed or audited "
    "in stage A"
)
_CRITERION_REASON = "no embedding clerk and no required chunks exist yet (stage D)"


async def evaluate(ctx: GateContext) -> GateResult:
    """Return the EMB gate result: `incomplete`, with no measurement attempted.

    Args:
        ctx: The run context (read for the criteria annex and the report directory only).

    Returns:
        A `GateResult` whose every criterion is `incomplete` (R3).
    """
    entries = incomplete_entries(ctx.criteria.by_gate.get(GATE, ()), _CRITERION_REASON)
    diagnostics = {
        "required_chunks": 0,
        "monitored_clerk_executions": 0,
        "embedding_clerk_present": False,
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
