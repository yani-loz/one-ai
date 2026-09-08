"""
Role: Seals EVID_NORM_V1 (contract §6, §1.4) by enumeration — every mapping of the frozen
      table, the unit model (one unit per surviving scalar, `…` → `...` as one unit of three,
      a whitespace run as one unit), the offset map with its boundary rule, UTF-8 byte offsets
      for Cyrillic and emoji, and the resolved / ambiguous / unresolved outcomes including the
      empty quote, the quote that normalizes to nothing, overlapping occurrences and the
      quote inside an expansion. The generated property lives in test_evid_norm_property.py.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.evid_norm and .exceptions (imported inside each test);
      tests.tools.mem01_verify.reference for the frozen character tables only.
Key invariants:
  - Expected spans are hand-computed from the §6 rules; the reference module is used only as
    the source of the character tables, never to produce an expected value here.
"""

from __future__ import annotations

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import InstrumentLoader

ELLIPSIS = chr(0x2026)
ZWSP = chr(0x200B)
ZWJ = chr(0x200D)
NBSP = chr(0x00A0)
EM_DASH = chr(0x2014)
LOW_9 = chr(0x201E)
HIGH_6 = chr(0x201C)
COMBINING_ACUTE = chr(0x0301)
CYRILLIC_SAMPLE = "Здравей, свят"


def test_version_constant(instrument: InstrumentLoader) -> None:
    assert instrument("evid_norm").EVID_NORM_VERSION == "EVID_NORM_V1"


@pytest.mark.parametrize("char", sorted(reference.DOUBLE_QUOTES))
def test_each_typographic_double_quote_maps_to_ascii(
    instrument: InstrumentLoader, char: str
) -> None:
    assert instrument("evid_norm").normalize(f"a{char}b").text == 'a"b'


@pytest.mark.parametrize("char", sorted(reference.SINGLE_QUOTES))
def test_each_single_quote_or_apostrophe_maps_to_ascii(
    instrument: InstrumentLoader, char: str
) -> None:
    assert instrument("evid_norm").normalize(f"a{char}b").text == "a'b"


@pytest.mark.parametrize("char", sorted(reference.DASHES))
def test_each_dash_maps_to_hyphen_minus(instrument: InstrumentLoader, char: str) -> None:
    assert instrument("evid_norm").normalize(f"a{char}b").text == "a-b"


@pytest.mark.parametrize("char", sorted(reference.REMOVED))
def test_each_zero_width_scalar_is_removed(instrument: InstrumentLoader, char: str) -> None:
    normalized = instrument("evid_norm").normalize(f"a{char}b")

    assert normalized.text == "ab" and normalized.source_positions == (0, 2)


@pytest.mark.parametrize("char", sorted(reference.WHITESPACE - {" "}))
def test_each_whitespace_scalar_becomes_one_ascii_space(
    instrument: InstrumentLoader, char: str
) -> None:
    assert instrument("evid_norm").normalize(f"a{char}b").text == "a b"


def test_whitespace_runs_collapse_and_edges_strip_while_case_and_cyrillic_survive(
    instrument: InstrumentLoader,
) -> None:
    evid_norm = instrument("evid_norm")

    normalized = evid_norm.normalize(f"  Здравей \t\r\n{NBSP} СВЯТ abc  ")

    assert normalized.text == "Здравей СВЯТ abc"
    assert normalized.original_len == len(f"  Здравей \t\r\n{NBSP} СВЯТ abc  ")


def test_ellipsis_is_one_unit_of_length_three(instrument: InstrumentLoader) -> None:
    normalized = instrument("evid_norm").normalize(f"a{ELLIPSIS}b")

    assert normalized.text == "a...b"
    assert normalized.source_positions == (0, 1, 1, 1, 2)
    assert normalized.unit_starts == frozenset({0, 1, 4})


def test_whitespace_run_is_one_unit_sourced_at_its_first_scalar(
    instrument: InstrumentLoader,
) -> None:
    normalized = instrument("evid_norm").normalize("a \t\r\n b")

    assert normalized.text == "a b"
    assert normalized.source_positions == (0, 1, 6)
    assert normalized.unit_starts == frozenset({0, 1, 2})


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [(0, 2, (0, 6)), (0, 3, (0, 7)), (2, 3, (6, 7)), (1, 2, (1, 6))],
)
def test_to_original_covers_the_whole_whitespace_run(
    instrument: InstrumentLoader, start: int, end: int, expected: tuple[int, int]
) -> None:
    normalized = instrument("evid_norm").normalize("a \t\r\n b")

    assert normalized.to_original(start, end) == expected


def test_to_original_never_includes_leading_or_trailing_deleted_or_trimmed_scalars(
    instrument: InstrumentLoader,
) -> None:
    evid_norm = instrument("evid_norm")

    inner = evid_norm.normalize(f"a{ZWSP}b")
    edges = evid_norm.normalize(f"{ZWSP}  ab {ZWJ}")

    assert inner.to_original(0, 2) == (0, 3)
    assert edges.text == "ab" and edges.to_original(0, 2) == (3, 5)


def test_to_original_rejects_a_boundary_inside_an_expansion(
    instrument: InstrumentLoader,
) -> None:
    evid_norm = instrument("evid_norm")
    exceptions = instrument("exceptions")
    normalized = evid_norm.normalize(f"x{ELLIPSIS}y")

    with pytest.raises(exceptions.NormalizationError):
        normalized.to_original(1, 2)
    with pytest.raises(exceptions.NormalizationError):
        normalized.to_original(2, 5)
    assert normalized.to_original(1, 4) == (1, 2)  # positive control: the whole unit


def test_source_positions_length_and_monotonicity(instrument: InstrumentLoader) -> None:
    text = f"„Здравей“{NBSP}{ELLIPSIS}{ZWSP} свят{EM_DASH}OK\r\n"

    normalized = instrument("evid_norm").normalize(text)

    assert len(normalized.source_positions) == len(normalized.text)
    assert list(normalized.source_positions) == sorted(normalized.source_positions)
    assert normalized.to_original(0, len(normalized.text)) == (0, len(text) - 2)


def test_utf8_byte_offsets_for_cyrillic_and_emoji(instrument: InstrumentLoader) -> None:
    evid_norm = instrument("evid_norm")

    assert evid_norm.utf8_byte_offsets(CYRILLIC_SAMPLE, 9, 13) == (16, 24)
    assert evid_norm.utf8_byte_offsets("a😀b", 1, 2) == (1, 5)
    assert evid_norm.utf8_byte_offsets("a😀b", 2, 3) == (5, 6)


def test_resolve_single_occurrence_is_resolved_with_scalar_and_byte_offsets(
    instrument: InstrumentLoader,
) -> None:
    evid_norm = instrument("evid_norm")

    resolution = evid_norm.resolve("свят", CYRILLIC_SAMPLE)

    assert resolution.kind == "resolved"
    assert resolution.spans == (evid_norm.Span(9, 13, 16, 24),)


def test_resolve_overlapping_occurrences_are_ambiguous_with_distinct_spans(
    instrument: InstrumentLoader,
) -> None:
    resolution = instrument("evid_norm").resolve("aa", "aaa")

    assert resolution.kind == "ambiguous"
    assert {(s.scalar_start, s.scalar_end) for s in resolution.spans} == {(0, 2), (1, 3)}
    assert len(resolution.spans) == 2


INSIDE_EXPANSION = {
    "occurrence_starts_inside_expansion",
    "occurrence_ends_inside_expansion",
}


@pytest.mark.parametrize(
    ("quote", "artifact", "reasons"),
    [
        ("", "abc", {"empty_quote"}),
        (ZWSP + ZWJ, "abc", {"normalizes_to_empty"}),
        ("  \r\n\t", "abc", {"normalizes_to_empty"}),
        (ELLIPSIS, "", {"no_unit_aligned_occurrence"}),
        ("xyz", "abc", {"no_unit_aligned_occurrence"}),
        (".", f"a{ELLIPSIS}b", INSIDE_EXPANSION),
        ("..", f"a{ELLIPSIS}b", INSIDE_EXPANSION),
        ("a.", f"a{ELLIPSIS}b", {"occurrence_ends_inside_expansion"}),
        (".b", f"a{ELLIPSIS}b", {"occurrence_starts_inside_expansion"}),
    ],
)
def test_resolve_unresolved_cases_carry_no_spans_and_the_determined_reason(
    instrument: InstrumentLoader, quote: str, artifact: str, reasons: set[str]
) -> None:
    evid_norm = instrument("evid_norm")

    resolution = evid_norm.resolve(quote, artifact)

    assert resolution.kind == "unresolved" and resolution.spans == ()
    assert resolution.reason in reasons, resolution.reason
    # positive control: a real quote in the same artifact resolves
    assert evid_norm.resolve("свят", CYRILLIC_SAMPLE).kind == "resolved"


def test_resolve_whole_expansion_matches_both_typographic_and_ascii_forms(
    instrument: InstrumentLoader,
) -> None:
    resolution = instrument("evid_norm").resolve("...", f"a{ELLIPSIS}b and c... d")

    assert resolution.kind == "ambiguous"
    assert {(s.scalar_start, s.scalar_end) for s in resolution.spans} == {(1, 2), (9, 12)}


@pytest.mark.parametrize(
    ("quote", "artifact", "span"),
    [
        ('"Здравей"', f"{LOW_9}Здравей{HIGH_6} свят", (0, 9)),
        ("a b", f"x a{NBSP}b y", (2, 5)),
        ("a-b", f"x a{EM_DASH}b y", (2, 5)),
        ("a b", "x a\r\n\tb y", (2, 7)),
        ("a b", "x a  b y", (2, 6)),
        (f"e{COMBINING_ACUTE}x", f"pre e{COMBINING_ACUTE}x post", (4, 7)),
        ("ab", f"a{ZWSP}b", (0, 3)),
    ],
)
def test_resolve_is_tolerant_of_typographic_variation_and_maps_to_original(
    instrument: InstrumentLoader, quote: str, artifact: str, span: tuple[int, int]
) -> None:
    evid_norm = instrument("evid_norm")

    resolution = evid_norm.resolve(quote, artifact)

    assert resolution.kind == "resolved"
    only = resolution.spans[0]
    assert (only.scalar_start, only.scalar_end) == span
    assert (only.byte_start, only.byte_end) == (
        len(artifact[: span[0]].encode("utf-8")),
        len(artifact[: span[1]].encode("utf-8")),
    )


def test_resolve_never_returns_a_span_ending_on_a_collapsed_space(
    instrument: InstrumentLoader,
) -> None:
    resolution = instrument("evid_norm").resolve("a ", "x a  b")

    assert resolution.kind == "resolved"
    assert (resolution.spans[0].scalar_start, resolution.spans[0].scalar_end) == (2, 3)


def test_normalized_and_resolution_types_are_frozen(instrument: InstrumentLoader) -> None:
    evid_norm = instrument("evid_norm")
    normalized = evid_norm.normalize("abc")
    resolution = evid_norm.resolve("b", "abc")

    with pytest.raises(AttributeError):
        normalized.text = "x"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        resolution.kind = "ambiguous"  # type: ignore[misc]


def test_normalization_error_is_a_mem01_error(instrument: InstrumentLoader) -> None:
    exceptions = instrument("exceptions")

    assert issubclass(exceptions.NormalizationError, exceptions.Mem01Error)
