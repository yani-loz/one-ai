"""
Role: The SNAP gate (snapshot hashing and reproducible replays) of contract §11 — the one gate
      Stage A expects to PASS, because the instrument owns both halves of its evidence: two clean
      replays of `SNAPSHOT_V1` over the unchanged corpus must produce identical per-artifact
      hashes, and every `EVID_NORM_V1` span-mapping fixture must resolve to the interval the
      battery independently specified.
Used by: `tools.mem01_verify.gates.registry` (`evaluate`); the pure surface `score_case` is sealed
      by `tests/tools/mem01_verify/test_gate_scoring.py`, the gate outcome by
      `test_gates_stage_a.py`.
Depends on: `tools.mem01_verify.gates.context`, `.criteria`, `.statuses`, `.evid_norm`
      (the resolver whose ACTUAL result is scored), `.snapshot` (`emit_snapshot`,
      `compare_manifests`) and `.fixtures.snap_cases` (data only).
Key invariants:
  - R12: the expected spans are the battery's, hand-derived from contract §6; `resolve` is
    invoked only to obtain the ACTUAL resolution.
  - Ambiguous spans compare as a SET of original intervals, never as an ordered sequence; for an
    `unresolved` expectation only the NON-EMPTINESS of the implementation's reason is scored
    (§1.4), never its wording.
  - The two replays are written UNDER the report directory (inside the run's declared closure,
    so no temporary-directory environment variable is read — §3.11) and the tree is deleted
    before the gate returns: the snapshot records carry verbatim corpus text, so nothing
    personal is left behind, and nothing but counts reaches the block (R5).
  - Both replays read ONE R6 snapshot session, so the comparison measures the emitter's
    determinism and never a change in the database underneath it.
"""

from __future__ import annotations

import shutil

from tools.mem01_verify import evid_norm, snapshot
from tools.mem01_verify.criteria import Criterion
from tools.mem01_verify.fixtures.snap_cases import SNAP_CASES, SnapCase
from tools.mem01_verify.gates.context import (
    CaseVerdict,
    GateContext,
    GateResult,
    criterion_entry,
    criterion_status,
    write_gate_report,
)
from tools.mem01_verify.statuses import ERROR, derive_gate_status

GATE = "SNAP"
#: §12: this gate never touches `ctx.probe`, so it alone never leases a probe.
NEEDS_PROBE: bool = False
REPLAY_CRITERION_ID = "snap.replay_hash_equality"
MAPPINGS_CRITERION_ID = "snap.source_span_mappings"

_GATE_REASON = (
    "the instrument owns both halves of this gate: two clean replays of SNAPSHOT_V1 and the "
    "EVID_NORM_V1 reverse span mapping over the public battery"
)
_NO_CORPUS_REASON = "this run never opened the corpus, so no replay was possible (R2)"
#: Scratch subdirectory of the report dir the two replays are written to and then removed.
_REPLAY_DIRNAME = "snap-replay"


def _span_key(span: object) -> tuple[int, int, int, int]:
    """The comparable identity of one span: its original scalar AND UTF-8 byte interval."""
    return (
        int(span.scalar_start),  # type: ignore[attr-defined]  # SnapSpan or evid_norm.Span
        int(span.scalar_end),  # type: ignore[attr-defined]
        int(span.byte_start),  # type: ignore[attr-defined]
        int(span.byte_end),  # type: ignore[attr-defined]
    )


def score_case(case: SnapCase, resolution: evid_norm.Resolution) -> CaseVerdict:
    """Score one span-mapping fixture against the resolution the resolver actually produced.

    Args:
        case: The fixture record carrying the independently specified expectation (§16.11).
        resolution: The ACTUAL `EVID_NORM_V1` resolution of the case's quote against its
            original text.

    Returns:
        A `CaseVerdict` that passes iff the kind matches AND the set of original intervals
        matches; for an `unresolved` expectation the implementation's reason must additionally
        be non-empty. Every mismatch contributes a defect naming the deviation.
    """
    defects: list[str] = []
    if resolution.kind != case.expected.kind:
        defects.append(
            f"kind {resolution.kind!r} where the fixture requires {case.expected.kind!r}"
        )
    expected_spans = {_span_key(span) for span in case.expected.spans}
    actual_spans = {_span_key(span) for span in resolution.spans}
    if actual_spans != expected_spans:
        defects.append(
            f"{len(actual_spans)} original interval(s) where the fixture specifies "
            f"{len(expected_spans)}, and the sets differ"
        )
    if case.expected.kind == "unresolved" and not resolution.reason:
        defects.append("unresolved without a reason (§1.4 requires a non-empty reason)")
    return CaseVerdict(case.case_id, case.criterion_id, not defects, tuple(defects))


def _decision_text(criterion: Criterion, numerator: int, denominator: int) -> str:
    """Render the measured ratio and the annex rule it was decided against (prose, not a rule)."""
    return f"{numerator}/{denominator} {criterion.operator} {criterion.threshold}"


def _score_mappings(criterion: Criterion) -> tuple[dict[str, object], tuple[CaseVerdict, ...]]:
    """Route every SNAP fixture through `resolve` + `score_case` and build its §3.4 entry."""
    verdicts: list[CaseVerdict] = []
    errors = 0
    for case in SNAP_CASES:
        try:
            verdicts.append(score_case(case, evid_norm.resolve(case.quote, case.original)))
        except Exception as exc:  # noqa: BLE001 - R2: a scoring failure never shrinks the denominator
            errors += 1
            verdicts.append(
                CaseVerdict(
                    case.case_id, case.criterion_id, False, (f"raised {type(exc).__name__}",)
                )
            )
    denominator = len(SNAP_CASES)
    numerator = sum(1 for verdict in verdicts if not verdict.passed)
    decision = _decision_text(criterion, numerator, denominator)
    entry = criterion_entry(
        criterion,
        reason=f"{decision}; fixtures whose reverse mapping returns a wrong original interval",
        numerator=numerator,
        denominator=denominator,
        expected=denominator,
        evaluated=denominator - errors,
        errors=errors,
        versions={"evid_norm": evid_norm.EVID_NORM_VERSION},
    )
    return entry, tuple(verdicts)


async def _replay_diff(ctx: GateContext) -> snapshot.DiffCounts:
    """Emit the org's snapshot twice into a scratch tree and compare the two manifests.

    Args:
        ctx: The run context; both emissions read ONE R6 snapshot session, so any difference is
            the emitter's non-determinism and never a change in the database.

    Returns:
        The `DiffCounts` of the two manifests. The scratch tree lives UNDER the report directory
        — inside the run's declared closure, so no temporary-directory environment variable is
        read (§3.11) — and is removed before this returns, because it holds verbatim corpus text.
    """
    root = ctx.report_dir / _REPLAY_DIRNAME
    try:
        async with ctx.corpus_snapshot() as session:  # type: ignore[misc]  # None handled by caller
            first = await snapshot.emit_snapshot(session, ctx.org_id, root / "replay-a")
            second = await snapshot.emit_snapshot(session, ctx.org_id, root / "replay-b")
        return snapshot.compare_manifests(first.manifest_path, second.manifest_path)
    finally:
        shutil.rmtree(root, ignore_errors=True)


async def _score_replay(
    ctx: GateContext, criterion: Criterion
) -> tuple[dict[str, object], dict[str, object]]:
    """Build the `snap.replay_hash_equality` entry from the two-replay comparison."""
    if ctx.corpus_snapshot is None:
        return criterion_entry(criterion, status=ERROR, reason=_NO_CORPUS_REASON), {
            "corpus_opened": False
        }
    diff = await _replay_diff(ctx)
    denominator = diff.added + diff.removed + diff.changed + diff.unchanged
    numerator = diff.added + diff.removed + diff.changed
    status = criterion_status(criterion, numerator=numerator, denominator=denominator)
    decision = _decision_text(criterion, numerator, denominator)
    entry = criterion_entry(
        criterion,
        status=status,
        reason=f"{decision}; artifacts whose hash differs between two clean replays",
        numerator=numerator,
        denominator=denominator,
        expected=denominator,
        evaluated=denominator,
        versions={"snapshot": snapshot.SNAPSHOT_VERSION},
    )
    diagnostics = {
        "corpus_opened": True,
        "replayed_artifacts": denominator,
        "added": diff.added,
        "removed": diff.removed,
        "changed": diff.changed,
        "unchanged": diff.unchanged,
    }
    return entry, diagnostics


async def evaluate(ctx: GateContext) -> GateResult:
    """Return the SNAP gate result: replay hash equality plus the reverse span mappings.

    Args:
        ctx: The run context.

    Returns:
        A `GateResult` carrying both §3.4 entries, the per-case verdicts of the mapping battery
        and the replay aggregates. This is the one gate Stage A expects to PASS.
    """
    by_id = {criterion.id: criterion for criterion in ctx.criteria.by_gate.get(GATE, ())}
    mapping_entry, verdicts = _score_mappings(by_id[MAPPINGS_CRITERION_ID])
    replay_entry, diagnostics = await _score_replay(ctx, by_id[REPLAY_CRITERION_ID])
    diagnostics["mapping_fixtures"] = len(SNAP_CASES)
    built = {str(entry["id"]): entry for entry in (replay_entry, mapping_entry)}
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
    )
