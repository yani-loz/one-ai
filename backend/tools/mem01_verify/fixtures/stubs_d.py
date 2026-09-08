"""Fixture record SHAPES for the deferred ATTR, ERASE and EMB gates.

Role:
    Declares the frozen dataclass schemas the stage-C/D/E batteries for forwarded-content
    attribution (ATTR), per-source erasure (ERASE) and the embedding clerk (EMB) will be
    authored against, plus a handful of SMOKE records that prove each schema is constructible
    and carries a real, independently specified expectation. Contract 10.9: in Stage A these
    gates ship their schema and print `incomplete`.
Used by:
    `tools.mem01_verify.fixtures.stubs` (re-export facade), the release schema emitter, and the
    ATTR / ERASE / EMB gate evaluators once their batteries exist.
Depends on:
    `tools.mem01_verify.fixtures.stubs_a` (`FixtureRecord`, `locate_fragment`). No database, no
    measured component, no third-party package.
Key invariants:
    - Every record is a frozen dataclass carrying `case_id`, `criterion_id`, `origin`,
      `expected` (contract 10) and `smoke`.
    - `smoke=True` marks a schema-proof record: it never enters a numerator, a denominator, or a
      criterion `minimum`.
    - `criterion_id` is always an id that exists in `release/criteria.step1.v1.yaml` for the
      record's own gate.
    - Expectations come from the criterion sheet (an attribution state is never NULL; a shared
      object with a remaining carrier survives; the clerk makes zero generative calls), never
      from running a measured component (contract R12).
    - PII-free and synthetic: addresses only under `example.test`, `acme.test`, `partner.test`;
      no real corpus text; Bulgarian and English cases both present.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .stubs_a import FixtureRecord, locate_fragment

# ------------------------------------------------------------------------------------- ATTR ---

AttributionState = Literal["recoverable", "ambiguous", "unresolvable"]


@dataclass(frozen=True, kw_only=True)
class AttributionExpectation:
    """The correct attribution tuple for one forwarded segment.

    Attributes:
        state: the mandatory state; never `None` (criterion `attr.provisional.c1_state_present`).
        original_author: the address that wrote the segment — non-`None` exactly when the state
            is `recoverable`.
        candidate_authors: the competing authors — addresses or display names exactly as they
            appear in the original — at least two when the state is `ambiguous`, empty otherwise.
        mandatory: whether this case carries a mandatory expectation and therefore enters the
            `attr.provisional.fixtures` denominator once the battery exists.
    """

    state: AttributionState
    original_author: str | None
    candidate_authors: tuple[str, ...]
    mandatory: bool


@dataclass(frozen=True, kw_only=True)
class AttributionCase(FixtureRecord[AttributionExpectation]):
    """One synthetic forwarded body with the scored segment marked by scalar offsets."""

    raw_body: str
    segment_start: int
    segment_end: int
    forwarding_sender: str


_ATTR_BODY_RECOVERABLE = (
    "FYI — see the supplier answer below.\n"
    "\n"
    "---------- Forwarded message ----------\n"
    "From: Ivan Petrov <ivan.petrov@partner.test>\n"
    "Date: Mon, 3 Mar 2025 11:30:00 +0200\n"
    "Subject: Re: Оферта 2025-114\n"
    "\n"
    "We can ship within ten working days.\n"
)

_ATTR_BODY_AMBIGUOUS = (
    "Препращам разговора.\n"
    "\n"
    "Иван Петров написа:\n"
    "> Мария Илиева написа:\n"
    "> > Срокът остава десет работни дни.\n"
)


ATTR_SMOKE_CASES: tuple[AttributionCase, ...] = (
    AttributionCase(
        case_id="attr-001",
        criterion_id="attr.provisional.fixtures",
        origin=(
            "ATTR criterion 15: a forwarded segment carrying an explicit `From:` header is "
            "recoverable, and its author is the forwarded sender, not the forwarder."
        ),
        smoke=True,
        raw_body=_ATTR_BODY_RECOVERABLE,
        segment_start=locate_fragment(
            _ATTR_BODY_RECOVERABLE, "We can ship within ten working days."
        )[0],
        segment_end=locate_fragment(_ATTR_BODY_RECOVERABLE, "We can ship within ten working days.")[
            1
        ],
        forwarding_sender="maya.ilieva@acme.test",
        expected=AttributionExpectation(
            state="recoverable",
            original_author="ivan.petrov@partner.test",
            candidate_authors=(),
            mandatory=True,
        ),
    ),
    AttributionCase(
        case_id="attr-002",
        criterion_id="attr.provisional.fixtures",
        origin=(
            "ATTR criterion 15: nested Bulgarian `написа:` attributions without addresses give "
            "two competing authors, so the mandatory state is `ambiguous`, never a guess."
        ),
        smoke=True,
        raw_body=_ATTR_BODY_AMBIGUOUS,
        segment_start=locate_fragment(_ATTR_BODY_AMBIGUOUS, "> > Срокът остава десет работни дни.")[
            0
        ],
        segment_end=locate_fragment(_ATTR_BODY_AMBIGUOUS, "> > Срокът остава десет работни дни.")[
            1
        ],
        forwarding_sender="office@acme.test",
        expected=AttributionExpectation(
            state="ambiguous",
            original_author=None,
            candidate_authors=("Иван Петров", "Мария Илиева"),
            mandatory=True,
        ),
    ),
)


# ------------------------------------------------------------------------------------ ERASE ---

ErasureScenarioKind = Literal["per_source_delete", "late_job_replay", "shared_object_control"]
DerivativeKind = Literal[
    "source_row",
    "carrier_edge",
    "grant",
    "provenance",
    "chunk",
    "vector",
    "job",
    "cache",
    "downstream",
]


@dataclass(frozen=True, kw_only=True)
class DerivedObject:
    """One derivative of a source, with every source that legitimately carries it."""

    object_id: str
    kind: DerivativeKind
    carrier_source_ids: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class ErasureExpectation:
    """What must and must not remain after the deletion completion barrier.

    Attributes:
        must_vanish_object_ids: derivatives whose only carrier was the deleted source; any
            survivor is a forbidden survivor.
        must_survive_object_ids: controls and shared objects with a remaining legitimate
            carrier; their disappearance is a wrong deletion.
        dangling_references_allowed: always 0 — a reference whose target is gone is a defect.
        republished_after_late_job: always `False` — a job finishing after the barrier must not
            resurrect deleted data.
    """

    must_vanish_object_ids: tuple[str, ...]
    must_survive_object_ids: tuple[str, ...]
    dangling_references_allowed: int
    republished_after_late_job: bool


@dataclass(frozen=True, kw_only=True)
class ErasureScenario(FixtureRecord[ErasureExpectation]):
    """One per-source deletion scenario over synthetic sources and their derivatives."""

    scenario_kind: ErasureScenarioKind
    org_label: Literal["A", "B"]
    source_ids: tuple[str, ...]
    deleted_source_id: str
    derived_objects: tuple[DerivedObject, ...]


ERASE_SMOKE_CASES: tuple[ErasureScenario, ...] = (
    ErasureScenario(
        case_id="erase-001",
        criterion_id="erase.no_forbidden_survivor",
        origin=(
            "ERASE criterion 7: deleting one source closes every derivative it alone carried, "
            "while a document that another source still carries must survive."
        ),
        smoke=True,
        scenario_kind="shared_object_control",
        org_label="A",
        source_ids=("erase-src-mailbox-a1", "erase-src-folder-a2"),
        deleted_source_id="erase-src-mailbox-a1",
        derived_objects=(
            DerivedObject(
                object_id="erase-chunk-0001",
                kind="chunk",
                carrier_source_ids=("erase-src-mailbox-a1",),
            ),
            DerivedObject(
                object_id="erase-grant-0001",
                kind="grant",
                carrier_source_ids=("erase-src-mailbox-a1",),
            ),
            DerivedObject(
                object_id="erase-doc-shared",
                kind="downstream",
                carrier_source_ids=("erase-src-mailbox-a1", "erase-src-folder-a2"),
            ),
        ),
        expected=ErasureExpectation(
            must_vanish_object_ids=("erase-chunk-0001", "erase-grant-0001"),
            must_survive_object_ids=("erase-doc-shared",),
            dangling_references_allowed=0,
            republished_after_late_job=False,
        ),
    ),
    ErasureScenario(
        case_id="erase-002",
        criterion_id="erase.no_resurrection",
        origin=(
            "ERASE criterion 7: a job admitted before the deletion and finishing after the "
            "completion barrier must publish nothing derived from the deleted source."
        ),
        smoke=True,
        scenario_kind="late_job_replay",
        org_label="A",
        source_ids=("erase-src-mailbox-a3",),
        deleted_source_id="erase-src-mailbox-a3",
        derived_objects=(
            DerivedObject(
                object_id="erase-job-late-0001",
                kind="job",
                carrier_source_ids=("erase-src-mailbox-a3",),
            ),
            DerivedObject(
                object_id="erase-vector-0001",
                kind="vector",
                carrier_source_ids=("erase-src-mailbox-a3",),
            ),
        ),
        expected=ErasureExpectation(
            must_vanish_object_ids=("erase-job-late-0001", "erase-vector-0001"),
            must_survive_object_ids=(),
            dangling_references_allowed=0,
            republished_after_late_job=False,
        ),
    ),
)


# -------------------------------------------------------------------------------------- EMB ---


@dataclass(frozen=True, kw_only=True)
class ClerkExecutionExpectation:
    """What a monitored embedding-clerk execution must and must not do.

    Attributes:
        generative_calls: always 0 — the clerk embeds, it never generates.
        unapproved_egress_events: always 0 — only declared endpoints may be reached.
        vector_dimensions: the pinned dimensionality every emitted vector must carry.
        model_version: the pinned embedding-model version stamped on every vector.
    """

    generative_calls: int
    unapproved_egress_events: int
    vector_dimensions: int
    model_version: str


@dataclass(frozen=True, kw_only=True)
class ClerkExecutionCase(FixtureRecord[ClerkExecutionExpectation]):
    """One monitored clerk execution over synthetic retrieval units."""

    input_unit_ids: tuple[str, ...]
    approved_endpoints: tuple[str, ...]
    offered_endpoints: tuple[str, ...]


EMB_SMOKE_CASES: tuple[ClerkExecutionCase, ...] = (
    ClerkExecutionCase(
        case_id="emb-001",
        criterion_id="emb.zero_generative_calls",
        origin=(
            "EMB criterion 17: a clerk execution over ordinary units makes zero generative LLM "
            "calls and stamps the pinned model version on every vector."
        ),
        smoke=True,
        input_unit_ids=("emb-unit-bg-0001", "emb-unit-en-0001"),
        approved_endpoints=("https://embeddings.internal.example.test/v1/embed",),
        offered_endpoints=("https://embeddings.internal.example.test/v1/embed",),
        expected=ClerkExecutionExpectation(
            generative_calls=0,
            unapproved_egress_events=0,
            vector_dimensions=1024,
            model_version="mem01-embed-v0",
        ),
    ),
    ClerkExecutionCase(
        case_id="emb-002",
        criterion_id="emb.zero_unapproved_egress",
        origin=(
            "EMB criterion 17: an undeclared endpoint offered alongside the approved one must "
            "never be reached — the lure proves the monitor, not the clerk's good intentions."
        ),
        smoke=True,
        input_unit_ids=("emb-unit-bg-0002",),
        approved_endpoints=("https://embeddings.internal.example.test/v1/embed",),
        offered_endpoints=(
            "https://embeddings.internal.example.test/v1/embed",
            "https://telemetry.partner.test/v1/collect",
        ),
        expected=ClerkExecutionExpectation(
            generative_calls=0,
            unapproved_egress_events=0,
            vector_dimensions=1024,
            model_version="mem01-embed-v0",
        ),
    ),
)
