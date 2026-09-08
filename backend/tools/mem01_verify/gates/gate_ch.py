"""
Role: The CH gate (chunking) of contract §11 — neither the chunker nor a pinned tokenizer exists
      in Stage A, so no chunk has ever been emitted, every CH criterion is `incomplete` and the
      gate is `incomplete`.
Used by: `tools.mem01_verify.gates.registry`; sealed by
      `tests/tools/mem01_verify/test_gates_stage_a.py` and `test_gates_registry.py`.
Depends on: `tools.mem01_verify.gates.context` (context, result and entry writers) and
      `.statuses` (the status algebra). No database: CH has no corpus evidence to scan.
Key invariants:
  - R3: a gate whose measured component does not exist prints `incomplete`, never PASS — and the
    denominator of every CH criterion (emitted chunks, checked boundaries) is structurally zero,
    which §3.4 makes ERROR rather than a vacuous pass, so nothing here may be "decided" at all.
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

GATE = "CH"
#: §12: this gate never touches `ctx.probe`, so it alone never leases a probe.
NEEDS_PROBE: bool = False
_GATE_REASON = "chunker absent and tokenizer unpinned: no chunk exists to audit in stage A"
_CRITERION_REASON = "no chunker and no pinned tokenizer exist yet (stage D)"


async def evaluate(ctx: GateContext) -> GateResult:
    """Return the CH gate result: `incomplete`, with no measurement attempted.

    Args:
        ctx: The run context (read for the criteria annex and the report directory only).

    Returns:
        A `GateResult` whose every criterion is `incomplete` (R3).
    """
    entries = incomplete_entries(ctx.criteria.by_gate.get(GATE, ()), _CRITERION_REASON)
    diagnostics = {
        "chunks_emitted": 0,
        "chunker_present": False,
        "tokenizer_pinned": False,
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
