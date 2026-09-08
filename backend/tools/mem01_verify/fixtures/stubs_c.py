"""Fixture record SHAPES for the deferred RET (retrieval) and THR (threading) gates.

Role:
    Declares the frozen dataclass schemas the stage-C/D batteries for cross-language retrieval
    (RET) and threading (THR) will be authored against, plus a handful of SMOKE records that
    prove each schema is constructible and carries a real, independently specified expectation.
    Contract 10.9: in Stage A these gates ship their schema and print `incomplete`.
Used by:
    `tools.mem01_verify.fixtures.stubs` (re-export facade), the release schema emitter, and the
    RET / THR gate evaluators once their batteries exist.
Depends on:
    `tools.mem01_verify.fixtures.stubs_a` (`FixtureRecord`). No database, no measured component,
    no third-party package.
Key invariants:
    - Every record is a frozen dataclass carrying `case_id`, `criterion_id`, `origin`,
      `expected` (contract 10) and `smoke`.
    - `smoke=True` marks a schema-proof record: it never enters a numerator, a denominator, or a
      criterion `minimum`.
    - `criterion_id` is always an id that exists in `release/criteria.step1.v1.yaml` for the
      record's own gate.
    - Expectations come from the criterion sheet (top-10 DISTINCT units for RET; header-borne
      joins and the tenant boundary for THR), never from running a measured component (R12).
    - PII-free and synthetic: addresses only under `example.test`, `acme.test`, `partner.test`;
      no real corpus text; Bulgarian and English cases both present.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .stubs_a import FixtureRecord

# -------------------------------------------------------------------------------------- RET ---

RetrievalDirection = Literal["bg_to_en", "en_to_bg", "same_language"]


@dataclass(frozen=True, kw_only=True)
class RetrievalExpectation:
    """The qrel for one retrieval query.

    Attributes:
        relevant_unit_ids: retrieval-unit ids that are FULLY relevant to the query; any one of
            them inside the first `k` distinct units is a hit.
        k: the cut-off in DISTINCT retrieval units (duplicates occupy positions).
        must_hit: `True` when the query must produce a hit; `False` marks a negative control
            whose relevant set is empty and for which any hit is a defect.
    """

    relevant_unit_ids: tuple[str, ...]
    k: int
    must_hit: bool


@dataclass(frozen=True, kw_only=True)
class RetrievalCase(FixtureRecord[RetrievalExpectation]):
    """One frozen-persona query with its language direction declared."""

    query_text: str
    query_language: Literal["bg", "en"]
    corpus_language: Literal["bg", "en"]
    direction: RetrievalDirection
    persona_label: str


RET_SMOKE_CASES: tuple[RetrievalCase, ...] = (
    RetrievalCase(
        case_id="ret-001",
        criterion_id="ret.hit_bg_to_en",
        origin=(
            "RET criterion 6, BG->EN direction: a Bulgarian query must reach the English unit "
            "that answers it within the first 10 distinct retrieval units."
        ),
        smoke=True,
        query_text="срок за доставка на офис оборудване",
        query_language="bg",
        corpus_language="en",
        direction="bg_to_en",
        persona_label="A1",
        expected=RetrievalExpectation(relevant_unit_ids=("ret-unit-en-0001",), k=10, must_hit=True),
    ),
    RetrievalCase(
        case_id="ret-002",
        criterion_id="ret.hit_en_to_bg",
        origin=(
            "RET criterion 6, EN->BG direction: an English query must reach the Bulgarian unit "
            "that answers it within the first 10 distinct retrieval units."
        ),
        smoke=True,
        query_text="delivery deadline for office equipment",
        query_language="en",
        corpus_language="bg",
        direction="en_to_bg",
        persona_label="A1",
        expected=RetrievalExpectation(relevant_unit_ids=("ret-unit-bg-0001",), k=10, must_hit=True),
    ),
)


# -------------------------------------------------------------------------------------- THR ---


@dataclass(frozen=True, kw_only=True)
class ThreadMessage:
    """One synthetic message header set used to build a threading pair.

    `sent_at` is an ISO-8601 instant in UTC (trailing `Z`); `message_id`, `in_reply_to` and the
    `references` entries are angle-bracketed RFC 5322 message identifiers.
    """

    message_key: str
    org_label: Literal["A", "B"]
    message_id: str
    in_reply_to: str | None
    references: tuple[str, ...]
    subject: str
    from_address: str
    sent_at: str


@dataclass(frozen=True, kw_only=True)
class ThreadPairExpectation:
    """Whether two synthetic messages belong to one production thread.

    Attributes:
        same_thread: `True` for a must-join pair, `False` for a must-not-join pair.
        join_evidence: which header carries the join — `none` when the pair must not join.
        reason: the rule this pair pins, in words.
    """

    same_thread: bool
    join_evidence: Literal["in_reply_to", "references", "none"]
    reason: str


@dataclass(frozen=True, kw_only=True)
class ThreadPairCase(FixtureRecord[ThreadPairExpectation]):
    """One ordered pair of synthetic messages with a declared ingest order."""

    relation: Literal["must_join", "must_not_join"]
    left: ThreadMessage
    right: ThreadMessage
    ingest_order: tuple[str, ...]


THR_SMOKE_CASES: tuple[ThreadPairCase, ...] = (
    ThreadPairCase(
        case_id="thr-001",
        criterion_id="thr.provisional.must_join_recall",
        origin=(
            "THR criterion 12: an In-Reply-To chain joins even when the reply is ingested first "
            "and the subject carries a localized `Re:` prefix."
        ),
        smoke=True,
        relation="must_join",
        left=ThreadMessage(
            message_key="thr-001-parent",
            org_label="A",
            message_id="<oferta-114-a@acme.test>",
            in_reply_to=None,
            references=(),
            subject="Оферта 2025-114",
            from_address="maya.ilieva@acme.test",
            sent_at="2025-03-03T09:00:00Z",
        ),
        right=ThreadMessage(
            message_key="thr-001-reply",
            org_label="A",
            message_id="<oferta-114-b@partner.test>",
            in_reply_to="<oferta-114-a@acme.test>",
            references=("<oferta-114-a@acme.test>",),
            subject="Re: Оферта 2025-114",
            from_address="ivan.petrov@partner.test",
            sent_at="2025-03-03T11:30:00Z",
        ),
        ingest_order=("thr-001-reply", "thr-001-parent"),
        expected=ThreadPairExpectation(
            same_thread=True,
            join_evidence="in_reply_to",
            reason="In-Reply-To names the parent Message-ID; ingest order is irrelevant.",
        ),
    ),
    ThreadPairCase(
        case_id="thr-002",
        criterion_id="thr.provisional.no_forbidden_merge",
        origin=(
            "THR criterion 12: an identical subject in two tenants is not a join, and thread "
            "membership never crosses a tenant boundary."
        ),
        smoke=True,
        relation="must_not_join",
        left=ThreadMessage(
            message_key="thr-002-org-a",
            org_label="A",
            message_id="<faktura-a@acme.test>",
            in_reply_to=None,
            references=(),
            subject="Фактура",
            from_address="maya.ilieva@acme.test",
            sent_at="2025-03-04T08:15:00Z",
        ),
        right=ThreadMessage(
            message_key="thr-002-org-b",
            org_label="B",
            message_id="<faktura-b@example.test>",
            in_reply_to=None,
            references=(),
            subject="Фактура",
            from_address="office@example.test",
            sent_at="2025-03-04T08:20:00Z",
        ),
        ingest_order=("thr-002-org-a", "thr-002-org-b"),
        expected=ThreadPairExpectation(
            same_thread=False,
            join_evidence="none",
            reason="Subject-only similarity across tenants is a forbidden merge.",
        ),
    ),
)
