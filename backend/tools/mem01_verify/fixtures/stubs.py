"""Re-export facade for the deferred-gate fixture schemas (QS, CH, NF, LANG, RET, THR, ATTR,
ERASE, EMB).

Role:
    One import site for every fixture record SHAPE whose battery is stage-B/C/D/E work
    (contract 10.9). The schemas themselves live in `stubs_a` (record head + QS + CH),
    `stubs_b` (NF + LANG), `stubs_c` (RET + THR) and `stubs_d` (ATTR + ERASE + EMB); the split
    exists only to keep every module well under the house file-size ceiling.
Used by:
    The release schema emitter, the gate evaluators for the nine deferred gates, and the
    stage-B fixture authors who will populate the batteries.
Depends on:
    `tools.mem01_verify.fixtures.stubs_a`, `.stubs_b`, `.stubs_c`, `.stubs_d` only.
Key invariants:
    - This module adds no records and no logic; it only re-exports. `SMOKE_CASE_TUPLES` is the
      complete list of smoke batteries, so a caller can assert that none of them ever reaches a
      criterion numerator, denominator or `minimum`.
    - Every re-exported record carries `case_id`, `criterion_id`, `origin`, `expected` and
      `smoke=True` (contract 10; smoke records are evidence of nothing).
"""

from __future__ import annotations

from .stubs_a import (
    CH_SMOKE_CASES,
    FIXTURES_VERSION,
    QS_SMOKE_CASES,
    ChunkBoundaryCase,
    ChunkBoundaryExpectation,
    FixtureRecord,
    QuoteLabel,
    QuoteSpan,
    QuoteStripCase,
    QuoteStripExpectation,
    locate_fragment,
)
from .stubs_b import (
    LANG_SMOKE_CASES,
    NF_SMOKE_CASES,
    DedupGroupCase,
    DedupGroupExpectation,
    DedupMember,
    LanguageCase,
    LanguageExpectation,
)
from .stubs_c import (
    RET_SMOKE_CASES,
    THR_SMOKE_CASES,
    RetrievalCase,
    RetrievalDirection,
    RetrievalExpectation,
    ThreadMessage,
    ThreadPairCase,
    ThreadPairExpectation,
)
from .stubs_d import (
    ATTR_SMOKE_CASES,
    EMB_SMOKE_CASES,
    ERASE_SMOKE_CASES,
    AttributionCase,
    AttributionExpectation,
    AttributionState,
    ClerkExecutionCase,
    ClerkExecutionExpectation,
    DerivativeKind,
    DerivedObject,
    ErasureExpectation,
    ErasureScenario,
    ErasureScenarioKind,
)

# `FixtureRecord` is intentionally left unparameterised here: the batteries carry nine different
# expectation types and the tuple is only ever iterated for the `smoke` flag and the id fields.
SMOKE_CASE_TUPLES: tuple[tuple[FixtureRecord, ...], ...] = (
    QS_SMOKE_CASES,
    CH_SMOKE_CASES,
    NF_SMOKE_CASES,
    LANG_SMOKE_CASES,
    RET_SMOKE_CASES,
    THR_SMOKE_CASES,
    ATTR_SMOKE_CASES,
    ERASE_SMOKE_CASES,
    EMB_SMOKE_CASES,
)

__all__ = [
    "ATTR_SMOKE_CASES",
    "CH_SMOKE_CASES",
    "EMB_SMOKE_CASES",
    "ERASE_SMOKE_CASES",
    "FIXTURES_VERSION",
    "LANG_SMOKE_CASES",
    "NF_SMOKE_CASES",
    "QS_SMOKE_CASES",
    "RET_SMOKE_CASES",
    "SMOKE_CASE_TUPLES",
    "THR_SMOKE_CASES",
    "AttributionCase",
    "AttributionExpectation",
    "AttributionState",
    "ChunkBoundaryCase",
    "ChunkBoundaryExpectation",
    "ClerkExecutionCase",
    "ClerkExecutionExpectation",
    "DedupGroupCase",
    "DedupGroupExpectation",
    "DedupMember",
    "DerivativeKind",
    "DerivedObject",
    "ErasureExpectation",
    "ErasureScenario",
    "ErasureScenarioKind",
    "FixtureRecord",
    "LanguageCase",
    "LanguageExpectation",
    "QuoteLabel",
    "QuoteSpan",
    "QuoteStripCase",
    "QuoteStripExpectation",
    "RetrievalCase",
    "RetrievalDirection",
    "RetrievalExpectation",
    "ThreadMessage",
    "ThreadPairCase",
    "ThreadPairExpectation",
    "locate_fragment",
]
