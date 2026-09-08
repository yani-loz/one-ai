"""SNAP battery entry point -- the public ``SNAP_CASES`` tuple for ``snap.source_span_mappings``.

Role: the single public surface of the SNAP fixture battery named in contract 1.3
    (``fixtures.snap_cases.SNAP_CASES``). It concatenates the two authored halves --
    ``snap_cases_a`` (baseline, typographic punctuation, whitespace collapse, trimming,
    zero-width) and ``snap_cases_b`` (astral emoji, combining marks, ambiguity, expansion
    boundaries) -- in ``case_id`` order, and re-exports the record types so a consumer needs one
    import. The split exists only to keep each module under the house file-size ceiling
    (``.claude/rules/code-quality.md`` A2); it carries no semantics.
Used by: the SNAP gate evaluator ``tools.mem01_verify.gates.gate_snap`` (contract 10.7),
    ``tools.mem01_verify.fixtures.digest.fixtures_digest`` (the battery digest enters
    ``config_hash``), and the instrument tests under ``backend/tests/tools/mem01_verify/``.
Depends on: ``tools.mem01_verify.fixtures.snap_cases_a`` and
    ``tools.mem01_verify.fixtures.snap_cases_b``. Nothing else -- data only, no measured
    component is imported or invoked (contract R12).
Key invariants:
    - ``SNAP_CASES`` holds at least the 30 fixtures the criterion's ``minimum`` requires (65
      today) and every ``case_id`` is unique and of the form ``snap-NNN``.
    - Every record carries ``criterion_id == "snap.source_span_mappings"`` -- this battery scores
      exactly one criterion.
    - Ordering is stable (part A then part B, ascending ``case_id``): the battery digest must not
      change because a case moved.
    - Expectations are hand-derived from contract section 6, never from running ``normalize`` or
      ``resolve``; where the current implementation disagrees the fixture is meant to FAIL.
"""

from __future__ import annotations

from tools.mem01_verify.fixtures.snap_cases_a import (
    SNAP_CASES_A,
    SNAP_CRITERION,
    ResolutionKind,
    SnapCase,
    SnapExpectation,
    SnapSpan,
)
from tools.mem01_verify.fixtures.snap_cases_b import SNAP_CASES_B

SNAP_CASES: tuple[SnapCase, ...] = SNAP_CASES_A + SNAP_CASES_B

__all__ = [
    "SNAP_CASES",
    "SNAP_CRITERION",
    "ResolutionKind",
    "SnapCase",
    "SnapExpectation",
    "SnapSpan",
]
