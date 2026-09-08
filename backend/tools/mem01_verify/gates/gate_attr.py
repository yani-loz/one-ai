"""
Role: The ATTR gate (forwarded-content attribution) of contract §11 — no attribution model exists
      in Stage A and forwarded segments cannot even be enumerated, so every ATTR criterion is
      `incomplete` except `attr.validation`, which is `pending` because it is scored only on the
      founder validation run; the gate is `incomplete`.
Used by: `tools.mem01_verify.gates.registry`; sealed by
      `tests/tools/mem01_verify/test_gates_stage_a.py` and `test_gates_registry.py`.
Depends on: `tools.mem01_verify.gates.context` (context, result and entry writers) and
      `.statuses` (the status algebra). No database: forwarded segments are not a stored concept.
Key invariants:
  - R3: no ATTR criterion prints PASS while the attribution model is absent.
  - ATTR is a HOLDOUT gate (§1.3 `HOLDOUT_GATES`): it stays in the verdict line's `provisional=`
    list for the whole loop, and its `.validation` entry never leaves `pending` outside the
    founder run (§3.4) — `incomplete_entries` writes that distinction, this module never
    special-cases it.
"""

from __future__ import annotations

from tools.mem01_verify.gates.context import (
    GateContext,
    GateResult,
    incomplete_entries,
    write_gate_report,
)
from tools.mem01_verify.statuses import derive_gate_status

GATE = "ATTR"
#: §12: this gate never touches `ctx.probe`, so it alone never leases a probe.
NEEDS_PROBE: bool = False
_GATE_REASON = (
    "attribution model absent: forwarded segments cannot be enumerated, so neither the fixture "
    "criterion nor the corpus invariants are measurable in stage A"
)
_CRITERION_REASON = "no attribution model exists yet and forwarded segments are not enumerable"


async def evaluate(ctx: GateContext) -> GateResult:
    """Return the ATTR gate result: `incomplete`, with its validation entry `pending`.

    Args:
        ctx: The run context (read for the criteria annex and the report directory only).

    Returns:
        A `GateResult` whose criteria are `incomplete` apart from the validation-split entry (R3).
    """
    entries = incomplete_entries(ctx.criteria.by_gate.get(GATE, ()), _CRITERION_REASON)
    diagnostics = {
        "forwarded_segments_enumerable": False,
        "attribution_model_present": False,
        "attr_fixtures_available": 0,
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
