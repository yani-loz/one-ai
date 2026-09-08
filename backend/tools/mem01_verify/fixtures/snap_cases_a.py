"""SNAP span-mapping fixtures, part A (cases snap-001 ... snap-026) and the shared record types.

Role: public, PII-free, synthetic F evidence for criterion ``snap.source_span_mappings`` -- each
    record pairs a synthetic ORIGINAL artifact text with a citation ``quote`` and the
    independently specified outcome of ``EVID_NORM_V1`` resolution (contract section 6): either
    the one correct ORIGINAL span (scalar AND UTF-8 byte offsets), all candidate spans when the
    quote is genuinely ambiguous, or an ``unresolved`` verdict. Part A covers the baseline,
    typographic-punctuation, whitespace-collapse, trimming and zero-width families; part B
    (``snap_cases_b``) continues with astral-plane emoji, combining marks, ambiguity and
    expansion-boundary families. This module also defines the record types both parts share.
Used by: ``tools.mem01_verify.fixtures.snap_cases`` (which re-exports ``SNAP_CASES``), and through
    it the SNAP gate evaluator ``tools.mem01_verify.gates.gate_snap`` and
    ``tools.mem01_verify.fixtures.digest.fixtures_digest``.
Depends on: nothing inside the project -- data only. It deliberately does NOT import
    ``tools.mem01_verify.evid_norm``: expectations are derived from the frozen rules of contract
    section 6 applied by hand to the synthetic originals authored here, never from running the
    instrument under test (contract R12, builder != labeler).
Key invariants:
    - Every expectation is hand-derived from contract section 6 and from the ORIGINAL text in the
      same record. No expected value was produced by ``normalize``, ``resolve``,
      ``utf8_byte_offsets`` or any other measured component.
    - ``scalar_start``/``scalar_end`` are Unicode-scalar offsets into ``original``;
      ``byte_start``/``byte_end`` are UTF-8 byte offsets into the SAME original text. A span is
      MINIMAL: leading and trailing scalars that normalization deleted (zero-width) or trimmed are
      excluded, while an interior deleted scalar and the WHOLE of an interior collapsed whitespace
      run fall inside the span -- the reverse mapping returns the right original interval and
      never claims a bijection after whitespace collapse.
    - Scoring contract for the evaluator: ``kind`` and ``spans`` are compared EXACTLY; ambiguous
      spans are listed here ascending by ``scalar_start`` and are to be compared as a SET of
      intervals, not as an ordered sequence. ``reason`` is a short stable documentation code --
      the only pass condition on it is that the implementation's own reason is non-empty (1.4).
    - ``resolved`` carries exactly one span, ``ambiguous`` at least two distinct original
      intervals, ``unresolved`` zero spans and a non-empty reason.
    - All text is synthetic and bilingual (Bulgarian and English); no real corpus text and no
      personal data. This battery needs no addresses at all.
    - Every typographic, invisible or whitespace-variant scalar is written as a backslash-u
      escape so a reviewer reads the code point instead of guessing at an invisible glyph.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SNAP_CRITERION = "snap.source_span_mappings"

ResolutionKind = Literal["resolved", "ambiguous", "unresolved"]


@dataclass(frozen=True, slots=True)
class SnapSpan:
    """One ORIGINAL-text interval, in Unicode scalars and in UTF-8 bytes.

    Both pairs address the ORIGINAL artifact text (never the normalized text) as a half-open
    ``[start, end)`` interval; the byte pair is the UTF-8 encoding of the same interval.
    """

    scalar_start: int
    scalar_end: int
    byte_start: int
    byte_end: int


@dataclass(frozen=True, slots=True)
class SnapExpectation:
    """The independently specified outcome of resolving one quote against one original.

    Mirrors the shape of ``evid_norm.Resolution`` without importing it: ``kind`` plus the spans
    that ``kind`` requires (1 for resolved, >= 2 for ambiguous, 0 for unresolved) plus a
    documentation ``reason`` that is non-empty exactly when ``kind`` is ``unresolved``.
    """

    kind: ResolutionKind
    spans: tuple[SnapSpan, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class SnapCase:
    """One SNAP normalization/mapping fixture.

    Attributes:
        case_id: Unique id of the form ``snap-NNN``.
        criterion_id: Always ``snap.source_span_mappings`` in this battery.
        origin: Why the case exists -- the contract section 6 rule it pins.
        original: The synthetic ORIGINAL artifact text the quote is resolved against.
        quote: The citation as a reader would paste it.
        expected: The hand-derived expectation (contract R12).
    """

    case_id: str
    criterion_id: str
    origin: str
    original: str
    quote: str
    expected: SnapExpectation


def resolved_span(
    scalar_start: int, scalar_end: int, byte_start: int, byte_end: int
) -> SnapExpectation:
    """Build the expectation for a quote that resolves to exactly one original span.

    Args:
        scalar_start: First Unicode scalar of the minimal original span.
        scalar_end: One past the last Unicode scalar of the minimal original span.
        byte_start: UTF-8 byte offset of ``scalar_start`` in the original text.
        byte_end: UTF-8 byte offset of ``scalar_end`` in the original text.

    Returns:
        A ``resolved`` expectation carrying that single span and an empty reason.
    """
    span = SnapSpan(scalar_start, scalar_end, byte_start, byte_end)
    return SnapExpectation(kind="resolved", spans=(span,), reason="")


def ambiguous_spans(*spans: tuple[int, int, int, int]) -> SnapExpectation:
    """Build the expectation for a quote with two or more distinct original intervals.

    Args:
        *spans: Candidate spans as ``(scalar_start, scalar_end, byte_start, byte_end)`` tuples,
            listed ascending by ``scalar_start``; the evaluator compares them as a set.

    Returns:
        An ``ambiguous`` expectation carrying every candidate span and an empty reason.
    """
    return SnapExpectation(
        kind="ambiguous",
        spans=tuple(SnapSpan(*span) for span in spans),
        reason="",
    )


def unresolved_with_reason(reason: str) -> SnapExpectation:
    """Build the expectation for a quote that has no unit-aligned occurrence.

    Args:
        reason: Stable documentation code -- ``empty_quote``, ``normalizes_to_empty``,
            ``no_unit_aligned_occurrence``, ``occurrence_starts_inside_expansion`` or
            ``occurrence_ends_inside_expansion``. Only non-emptiness is scored.

    Returns:
        An ``unresolved`` expectation with zero spans.
    """
    return SnapExpectation(kind="unresolved", spans=(), reason=reason)


SNAP_CASES_A: tuple[SnapCase, ...] = (
    SnapCase(
        case_id="snap-001",
        criterion_id=SNAP_CRITERION,
        origin="baseline: an ASCII quote present verbatim resolves to its own scalar span",
        original="Invoice total is 120 EUR.",
        quote="total is 120",
        expected=resolved_span(8, 20, 8, 20),
    ),
    SnapCase(
        case_id="snap-002",
        criterion_id=SNAP_CRITERION,
        origin="Cyrillic is never transliterated; byte offsets count 2 bytes per Cyrillic scalar",
        original="Договорът е подписан на 5 март.",
        quote="подписан",
        expected=resolved_span(12, 20, 22, 38),
    ),
    SnapCase(
        case_id="snap-003",
        criterion_id=SNAP_CRITERION,
        origin="Bulgarian \\u201e/\\u201c double quotes normalize to a straight double quote",
        original="Той каза \u201eготово\u201c вчера.",
        quote='"готово"',
        expected=resolved_span(9, 17, 16, 34),
    ),
    SnapCase(
        case_id="snap-004",
        criterion_id=SNAP_CRITERION,
        origin="en dash \\u2013 normalizes to '-' (one unit, one scalar, three original bytes)",
        original="Q1 2026 \u2013 Q2 2026 results",
        quote="Q1 2026 - Q2 2026",
        expected=resolved_span(0, 17, 0, 19),
    ),
    SnapCase(
        case_id="snap-005",
        criterion_id=SNAP_CRITERION,
        origin="em dash \\u2014 normalizes to '-' with no surrounding whitespace involved",
        original="Payment\u2014overdue",
        quote="Payment-overdue",
        expected=resolved_span(0, 15, 0, 17),
    ),
    SnapCase(
        case_id="snap-006",
        criterion_id=SNAP_CRITERION,
        origin="minus sign \\u2212 is in the frozen dash table and normalizes to '-'",
        original="Delta \u22125 units",
        quote="Delta -5",
        expected=resolved_span(0, 8, 0, 10),
    ),
    SnapCase(
        case_id="snap-007",
        criterion_id=SNAP_CRITERION,
        origin="non-breaking hyphen \\u2011 normalizes to '-' and is NOT treated as whitespace",
        original="Ref X\u20112026 approved",
        quote="X-2026",
        expected=resolved_span(4, 10, 4, 12),
    ),
    SnapCase(
        case_id="snap-008",
        criterion_id=SNAP_CRITERION,
        origin="ellipsis \\u2026 expands to '...' as ONE unit of length 3; its span is one scalar",
        original="Ще проверя\u2026утре",
        quote="проверя...утре",
        expected=resolved_span(3, 15, 5, 30),
    ),
    SnapCase(
        case_id="snap-009",
        criterion_id=SNAP_CRITERION,
        origin="'.' must NOT match inside the \\u2026 expansion -- no occurrence is unit-aligned",
        original="Wait\u2026 done",
        quote=".",
        expected=unresolved_with_reason("no_unit_aligned_occurrence"),
    ),
    SnapCase(
        case_id="snap-010",
        criterion_id=SNAP_CRITERION,
        origin="positive twin of snap-009: '...' consumes the whole \\u2026 unit, span = 1 scalar",
        original="Wait\u2026 done",
        quote="...",
        expected=resolved_span(4, 5, 4, 7),
    ),
    SnapCase(
        case_id="snap-011",
        criterion_id=SNAP_CRITERION,
        origin="normalization runs on BOTH sides: curly quotes in the QUOTE match straight ones",
        original='Той написа "край" накрая.',
        quote="\u201cкрай\u201d",
        expected=resolved_span(11, 17, 20, 30),
    ),
    SnapCase(
        case_id="snap-012",
        criterion_id=SNAP_CRITERION,
        origin="guillemets \\u00ab/\\u00bb normalize to a double quote and are 2 bytes each, not 3",
        original="Проектът \u00abОдисей\u00bb стартира",
        quote='"Одисей"',
        expected=resolved_span(9, 17, 17, 33),
    ),
    SnapCase(
        case_id="snap-013",
        criterion_id=SNAP_CRITERION,
        origin="right single quotation mark \\u2019 normalizes to the ASCII apostrophe",
        original="It\u2019s the client\u2019s copy",
        quote="It's the client's",
        expected=resolved_span(0, 17, 0, 21),
    ),
    SnapCase(
        case_id="snap-014",
        criterion_id=SNAP_CRITERION,
        origin="CRLF collapses to one space unit; the span covers the WHOLE interior run",
        original="Здравей,\r\nсвят",
        quote="Здравей, свят",
        expected=resolved_span(0, 14, 0, 25),
    ),
    SnapCase(
        case_id="snap-015",
        criterion_id=SNAP_CRITERION,
        origin="a three-space run is one unit; the reverse mapping never claims a bijection",
        original="Total:   120 EUR",
        quote="Total: 120",
        expected=resolved_span(0, 12, 0, 12),
    ),
    SnapCase(
        case_id="snap-016",
        criterion_id=SNAP_CRITERION,
        origin="NBSP \\u00a0 becomes a space; byte offsets still count its 2 original bytes",
        original="120\u00a0EUR net",
        quote="120 EUR",
        expected=resolved_span(0, 7, 0, 8),
    ),
    SnapCase(
        case_id="snap-017",
        criterion_id=SNAP_CRITERION,
        origin="mixed run (\\u202f + \\u2009 + tab) collapses to ONE unit spanning three scalars",
        original="Сума:\u202f\u2009\t1200 лв.",
        quote="Сума: 1200",
        expected=resolved_span(0, 12, 0, 20),
    ),
    SnapCase(
        case_id="snap-018",
        criterion_id=SNAP_CRITERION,
        origin="leading/trailing whitespace in the QUOTE is stripped; the span stays minimal",
        original="Ключова дума тук",
        quote="  дума  ",
        expected=resolved_span(8, 12, 15, 23),
    ),
    SnapCase(
        case_id="snap-019",
        criterion_id=SNAP_CRITERION,
        origin="trimmed leading/trailing scalars of the ORIGINAL are never inside the span",
        original="\n\n  Отчет за март  \n",
        quote="Отчет за март",
        expected=resolved_span(4, 17, 4, 28),
    ),
    SnapCase(
        case_id="snap-020",
        criterion_id=SNAP_CRITERION,
        origin="a whitespace-only quote normalizes to the empty string -- unresolved, not a match",
        original="Нещо тук",
        quote="   \n ",
        expected=unresolved_with_reason("normalizes_to_empty"),
    ),
    SnapCase(
        case_id="snap-021",
        criterion_id=SNAP_CRITERION,
        origin="an empty quote is unresolved by rule, not a zero-length match at position 0",
        original="Anything",
        quote="",
        expected=unresolved_with_reason("empty_quote"),
    ),
    SnapCase(
        case_id="snap-022",
        criterion_id=SNAP_CRITERION,
        origin="soft hyphen \\u00ad is deleted; an INTERIOR deleted scalar stays inside the span",
        original="Дого\u00adвор подписан",
        quote="Договор",
        expected=resolved_span(0, 8, 0, 16),
    ),
    SnapCase(
        case_id="snap-023",
        criterion_id=SNAP_CRITERION,
        origin="a LEADING deleted zero-width space \\u200b is never included in the minimal span",
        original="A\u200bBCD",
        quote="BCD",
        expected=resolved_span(2, 5, 4, 7),
    ),
    SnapCase(
        case_id="snap-024",
        criterion_id=SNAP_CRITERION,
        origin="a TRAILING deleted zero-width joiner \\u200d is never included in the span",
        original="OK\u200d done",
        quote="OK",
        expected=resolved_span(0, 2, 0, 2),
    ),
    SnapCase(
        case_id="snap-025",
        criterion_id=SNAP_CRITERION,
        origin="a BOM \\ufeff at scalar 0 is deleted: it shifts the byte offsets, not the scalars",
        original="\ufeffОтчет готов",
        quote="Отчет",
        expected=resolved_span(1, 6, 3, 13),
    ),
    SnapCase(
        case_id="snap-026",
        criterion_id=SNAP_CRITERION,
        origin="a quote of only zero-width scalars normalizes to empty (distinct from snap-020)",
        original="Каталог готов",
        quote="\u200b\u200d",
        expected=unresolved_with_reason("normalizes_to_empty"),
    ),
)
