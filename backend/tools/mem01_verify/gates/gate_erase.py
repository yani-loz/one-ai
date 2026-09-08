"""
Role: The ERASE gate (per-source deletion) of contract §11 — no erasure pipeline and no deletion
      scenarios exist in Stage A, so every ERASE criterion is `incomplete` and the gate is
      `incomplete`.
Used by: `tools.mem01_verify.gates.registry`; sealed by
      `tests/tools/mem01_verify/test_gates_stage_a.py` and `test_gates_registry.py`.
Depends on: `tools.mem01_verify.gates.context` (context, result and entry writers) and
      `.statuses` (the status algebra). No database read: see the invariant below.
Key invariants:
  - R3: no ERASE criterion prints PASS while the erasure component is absent. §11 records ERASE's
    stage-A evidence as NONE, and `erase.no_dangling_references` — the one corpus-scannable
    criterion — carries an annex `minimum` of 1,000 scanned references over a REGISTERED relation
    set that the erasure component itself would define; scanning an ad-hoc relation set instead
    would either invent a denominator or fall below the minimum (ERROR under R2). Stage A
    therefore leaves it `incomplete` with the rest of the gate rather than scoring a denominator
    no frozen rule fixes.
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

GATE = "ERASE"
#: §12: this gate never touches `ctx.probe`, so it alone never leases a probe.
NEEDS_PROBE: bool = False
_GATE_REASON = (
    "erasure pipeline absent: no per-source deletion scenario can be run and no registered "
    "derived-relation set exists to scan in stage A"
)
_CRITERION_REASON = (
    "no erasure component, no deletion scenarios and no registered derived-relation set exist "
    "yet (stage E)"
)


async def evaluate(ctx: GateContext) -> GateResult:
    """Return the ERASE gate result: `incomplete`, with no measurement attempted.

    Args:
        ctx: The run context (read for the criteria annex and the report directory only).

    Returns:
        A `GateResult` whose every criterion is `incomplete` (R3).
    """
    entries = incomplete_entries(ctx.criteria.by_gate.get(GATE, ()), _CRITERION_REASON)
    diagnostics = {
        "deletion_scenarios_available": 0,
        "erasure_component_present": False,
        "registered_derived_relations": 0,
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
