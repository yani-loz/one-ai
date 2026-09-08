"""Fixture record SHAPES — shared record head, plus the QS and CH deferred gates.

Role:
    Declares `FixtureRecord` (the common head every MEM-01 fixture record inherits),
    `locate_fragment` (the fixture-authoring span helper), and the frozen dataclass schemas the
    stage-B/C batteries for quote stripping (QS) and chunking (CH) will be authored against,
    plus a handful of SMOKE records that prove each schema is constructible and carries a real,
    independently specified expectation. Contract 10.9: in Stage A these gates ship their schema
    (so the release schemas are complete) and print `incomplete`.
Used by:
    `tools.mem01_verify.fixtures.stubs` (re-export facade), the sibling schema modules
    `stubs_b` / `stubs_c` / `stubs_d` (they inherit `FixtureRecord` from here), the release
    schema emitter, and the QS / CH gate evaluators once their batteries exist.
Depends on:
    `tools.mem01_verify.exceptions.FixtureError` only. No database, no measured component, no
    third-party package.
Key invariants:
    - Every record is a frozen dataclass carrying `case_id`, `criterion_id`, `origin`,
      `expected` (contract 10) and `smoke`.
    - `smoke=True` marks a schema-proof record. A smoke record is EVIDENCE OF NOTHING: it never
      enters a numerator, a denominator, or a `minimum` count.
    - `criterion_id` is always an id that exists in `release/criteria.step1.v1.yaml` for the
      record's own gate.
    - Expectations are specified from the criterion sheet and from the synthetic ORIGINAL text
      declared in this module; they are NEVER obtained by running a measured component
      (contract R12). `locate_fragment` performs literal lookup over that original only.
    - PII-free and synthetic: addresses only under `example.test`, `acme.test`, `partner.test`;
      no real corpus text; Bulgarian and English cases both present.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..exceptions import FixtureError

FIXTURES_VERSION = "FIXTURES_V1"


def locate_fragment(text: str, fragment: str) -> tuple[int, int]:
    """Return the Unicode-scalar span of a fragment inside a synthetic original.

    Args:
        text: the authored synthetic original.
        fragment: a literal substring that must occur EXACTLY once in `text`.

    Returns:
        `(start, end)` scalar offsets, end-exclusive.

    Raises:
        FixtureError: the fragment is absent, or occurs more than once (an ambiguous
            occurrence would silently mislabel the span, and the QS criterion is scored by
            occurrence, not by substring identity).
    """
    occurrences = text.count(fragment)
    if occurrences != 1:
        raise FixtureError(
            f"fixture fragment must occur exactly once, found {occurrences}: {fragment!r}"
        )
    start = text.index(fragment)
    return start, start + len(fragment)


@dataclass(frozen=True, kw_only=True)
class FixtureRecord[ExpectedT]:
    """Common head of every MEM-01 fixture record.

    Attributes:
        case_id: unique `<gate>-NNN` identifier across the whole fixture package.
        criterion_id: the criterion this case pins, as spelled in the criteria file.
        origin: why the case exists — the rule, RFC clause or policy line it pins.
        expected: the independently specified expectation (never a measured output).
        smoke: `True` for a schema-proof record that no criterion may count.
    """

    case_id: str
    criterion_id: str
    origin: str
    expected: ExpectedT
    smoke: bool = False


# --------------------------------------------------------------------------------------- QS ---

QuoteLabel = Literal["new_content", "redundant_quote", "signature"]


@dataclass(frozen=True, kw_only=True)
class QuoteSpan:
    """A labeled scalar span of a synthetic email body.

    Attributes:
        start: inclusive Unicode-scalar offset into the case's `raw_body`.
        end: exclusive Unicode-scalar offset.
        label: what the span is, per the QS criterion sheet.
        fragment: the exact original text the span covers (redundancy guard for reviewers).
    """

    start: int
    end: int
    label: QuoteLabel
    fragment: str


@dataclass(frozen=True, kw_only=True)
class QuoteStripExpectation:
    """What a correct quote stripper must and must not leave on every `qs_surface`.

    Scalar masses are the summed lengths of the spans below; they are not restated as separate
    integers so that spans and counts can never disagree.

    Attributes:
        must_survive: new-content occurrences that must be present at their mapped position on
            EVERY surface named in `qs_surfaces` (criterion `qs.no_content_loss`).
        must_not_survive: redundant-quote and signature occurrences that must reach no surface.
        email_has_quote: whether this email belongs to the `qs.echo_incidence` denominator.
    """

    must_survive: tuple[QuoteSpan, ...]
    must_not_survive: tuple[QuoteSpan, ...]
    email_has_quote: bool


@dataclass(frozen=True, kw_only=True)
class QuoteStripCase(FixtureRecord[QuoteStripExpectation]):
    """One synthetic email with hand-labeled new-content / quote / signature spans."""

    raw_body: str
    subject: str
    language: Literal["bg", "en", "mixed", "und"]
    labeled_spans: tuple[QuoteSpan, ...]


_QS_BODY_EN = (
    "Thanks, the invoice is approved.\n"
    "\n"
    "On Mon, 3 Mar 2025, Ivan Petrov <ivan.petrov@partner.test> wrote:\n"
    "> Please confirm the invoice.\n"
    "\n"
    "--\n"
    "Maya Ilieva\n"
    "Acme Test EOOD\n"
)

_QS_BODY_BG = (
    "Потвърждавам сумата по фактурата.\n"
    "\n"
    "На 3 март 2025 г. Иван Петров <ivan.petrov@partner.test> написа:\n"
    "> Моля потвърдете сумата по фактура 2025-114.\n"
    "\n"
    "--\n"
    "Мария Илиева\n"
    "Акме Тест ЕООД\n"
)


def _quote_span(body: str, fragment: str, label: QuoteLabel) -> QuoteSpan:
    start, end = locate_fragment(body, fragment)
    return QuoteSpan(start=start, end=end, label=label, fragment=fragment)


_QS_EN_NEW = _quote_span(_QS_BODY_EN, "Thanks, the invoice is approved.", "new_content")
_QS_EN_QUOTE = _quote_span(
    _QS_BODY_EN,
    "On Mon, 3 Mar 2025, Ivan Petrov <ivan.petrov@partner.test> wrote:\n"
    "> Please confirm the invoice.",
    "redundant_quote",
)
_QS_EN_SIG = _quote_span(_QS_BODY_EN, "--\nMaya Ilieva\nAcme Test EOOD", "signature")

_QS_BG_NEW = _quote_span(_QS_BODY_BG, "Потвърждавам сумата по фактурата.", "new_content")
_QS_BG_QUOTE = _quote_span(
    _QS_BODY_BG,
    "На 3 март 2025 г. Иван Петров <ivan.petrov@partner.test> написа:\n"
    "> Моля потвърдете сумата по фактура 2025-114.",
    "redundant_quote",
)
_QS_BG_SIG = _quote_span(_QS_BODY_BG, "--\nМария Илиева\nАкме Тест ЕООД", "signature")


QS_SMOKE_CASES: tuple[QuoteStripCase, ...] = (
    QuoteStripCase(
        case_id="qs-001",
        criterion_id="qs.no_content_loss",
        origin=(
            "QS criterion 1: the new-content occurrence must survive on every qs_surface while "
            "the attribution line, the quoted reply and the delimiter signature must reach none."
        ),
        smoke=True,
        raw_body=_QS_BODY_EN,
        subject="Invoice 2025-114",
        language="en",
        labeled_spans=(_QS_EN_NEW, _QS_EN_QUOTE, _QS_EN_SIG),
        expected=QuoteStripExpectation(
            must_survive=(_QS_EN_NEW,),
            must_not_survive=(_QS_EN_QUOTE, _QS_EN_SIG),
            email_has_quote=True,
        ),
    ),
    QuoteStripCase(
        case_id="qs-002",
        criterion_id="qs.echo_incidence",
        origin=(
            "QS criterion 1, Bulgarian surface: the 'написа:' attribution marker must be "
            "stripped exactly like the English 'wrote:' marker, so a BG email retaining it is "
            "an echo incidence."
        ),
        smoke=True,
        raw_body=_QS_BODY_BG,
        subject="Фактура 2025-114",
        language="bg",
        labeled_spans=(_QS_BG_NEW, _QS_BG_QUOTE, _QS_BG_SIG),
        expected=QuoteStripExpectation(
            must_survive=(_QS_BG_NEW,),
            must_not_survive=(_QS_BG_QUOTE, _QS_BG_SIG),
            email_has_quote=True,
        ),
    ),
)


# --------------------------------------------------------------------------------------- CH ---


@dataclass(frozen=True, kw_only=True)
class ChunkBoundaryExpectation:
    """Legal and forbidden chunk boundaries over one synthetic source unit.

    Attributes:
        legal_boundary_starts: scalar offsets at which a chunk may begin.
        forbidden_boundary_starts: offsets a boundary must never fall on (mid-sentence cuts,
            invented section boundaries, unlabeled header repeats).
        required_covered_spans: `(start, end)` spans that some emitted chunk must cover in full.
        max_overlap_scalars: the frozen overlap budget for this unit.
        min_chunks: fewest chunks a correct chunker may emit.
        max_chunks: most chunks a correct chunker may emit (anti-fragmentation ceiling).
    """

    legal_boundary_starts: tuple[int, ...]
    forbidden_boundary_starts: tuple[int, ...]
    required_covered_spans: tuple[tuple[int, int], ...]
    max_overlap_scalars: int
    min_chunks: int
    max_chunks: int


@dataclass(frozen=True, kw_only=True)
class ChunkBoundaryCase(FixtureRecord[ChunkBoundaryExpectation]):
    """One synthetic source unit with its true section structure declared up front."""

    unit_kind: Literal["email_body", "email_subject", "attachment_text"]
    source_text: str
    true_section_starts: tuple[int, ...]
    repeated_header: str | None


_CH_SOURCE_EN = (
    "Delivery Terms\n"
    "The supplier ships within ten working days of the confirmed order.\n"
    "\n"
    "Payment Terms\n"
    "The buyer pays within thirty days of the invoice date.\n"
)

_CH_SOURCE_BG = "Фактура 2025-114 е платена изцяло на 12 март 2025 г.\n"


CH_SMOKE_CASES: tuple[ChunkBoundaryCase, ...] = (
    ChunkBoundaryCase(
        case_id="ch-001",
        criterion_id="ch.no_illegal_boundaries",
        origin=(
            "CH criterion 2: a boundary may fall on a true section start and never mid-sentence; "
            "a two-section document under the cap yields at most one chunk per section."
        ),
        smoke=True,
        unit_kind="attachment_text",
        source_text=_CH_SOURCE_EN,
        true_section_starts=(
            locate_fragment(_CH_SOURCE_EN, "Delivery Terms")[0],
            locate_fragment(_CH_SOURCE_EN, "Payment Terms")[0],
        ),
        repeated_header=None,
        expected=ChunkBoundaryExpectation(
            legal_boundary_starts=(
                locate_fragment(_CH_SOURCE_EN, "Delivery Terms")[0],
                locate_fragment(_CH_SOURCE_EN, "Payment Terms")[0],
            ),
            forbidden_boundary_starts=(locate_fragment(_CH_SOURCE_EN, "within thirty days")[0],),
            required_covered_spans=((0, len(_CH_SOURCE_EN)),),
            max_overlap_scalars=0,
            min_chunks=1,
            max_chunks=2,
        ),
    ),
    ChunkBoundaryCase(
        case_id="ch-002",
        criterion_id="ch.no_illegal_boundaries",
        origin=(
            "CH anti-fragmentation, Bulgarian: an input far below the cap must stay a single "
            "chunk; splitting it is a packing violation and an invented boundary."
        ),
        smoke=True,
        unit_kind="email_body",
        source_text=_CH_SOURCE_BG,
        true_section_starts=(0,),
        repeated_header=None,
        expected=ChunkBoundaryExpectation(
            legal_boundary_starts=(0,),
            forbidden_boundary_starts=(locate_fragment(_CH_SOURCE_BG, "платена изцяло")[0],),
            required_covered_spans=((0, len(_CH_SOURCE_BG)),),
            max_overlap_scalars=0,
            min_chunks=1,
            max_chunks=1,
        ),
    ),
)
