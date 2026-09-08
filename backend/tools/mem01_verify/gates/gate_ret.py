"""
Role: The RET gate (cross-language retrieval) of contract §11 — there is no retrieval unit to
      rank in Stage A (no chunks, no embeddings) and no labeled query split, so every RET
      criterion is `incomplete` and the gate is `incomplete`.
Used by: `tools.mem01_verify.gates.registry` (`evaluate`) and the runner's hidden-scorability
      check (`hidden_scorable`, §16.13); sealed by `tests/tools/mem01_verify/test_gates_stage_a.py`
      and `test_gates_registry.py`.
Depends on: `tools.mem01_verify.gates.context` (context, result and entry writers) and
      `.statuses` (the status algebra). No database: RET has no corpus evidence to scan.
Key invariants:
  - R3: no RET criterion prints PASS while retrieval does not exist; every criterion is H-test
    evidence whose labels have not been authored.
  - `hidden_scorable()` is False for the whole of Stage A, so a `--checkpoint` run must not
    reserve a RET hidden unit (§16.13).
"""

from __future__ import annotations

from tools.mem01_verify.gates.context import (
    GateContext,
    GateResult,
    incomplete_entries,
    write_gate_report,
)
from tools.mem01_verify.statuses import derive_gate_status

GATE = "RET"
#: §12: this gate never touches `ctx.probe`, so it alone never leases a probe.
NEEDS_PROBE: bool = False
_GATE_REASON = (
    "retrieval absent (no chunks, no embeddings) and the RET H split carries no labeled queries: "
    "no criterion is measurable in stage A"
)
_CRITERION_REASON = "no retrieval component and no labeled query split exist yet (stage D)"


def hidden_scorable() -> bool:
    """False in stage A: RET has no measured component and no labeled hidden evidence (§16.13)."""
    return False


async def evaluate(ctx: GateContext) -> GateResult:
    """Return the RET gate result: `incomplete`, with no measurement attempted.

    Args:
        ctx: The run context (read for the criteria annex and the report directory only).

    Returns:
        A `GateResult` whose every criterion is `incomplete` (R3).
    """
    entries = incomplete_entries(ctx.criteria.by_gate.get(GATE, ()), _CRITERION_REASON)
    diagnostics = {
        "labeled_queries_available": 0,
        "retrieval_component_present": False,
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
