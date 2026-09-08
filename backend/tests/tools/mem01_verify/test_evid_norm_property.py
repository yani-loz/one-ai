"""
Role: The generated round-trip property of contract §6 over EVID_NORM_V1: for seeded random
      texts (Bulgarian, English, mixed, typographic punctuation, NBSP and friends, CRLF, emoji
      including a ZWJ sequence, combining marks, zero-width scalars, repeated fragments) and
      random substrings, (i) resolution is resolved or ambiguous, (ii) the trimmed substring
      equals the trimmed text of one returned span, (iii) every span normalizes to the quote,
      (iv) the offset map is monotone and covers [first_kept, last_kept + 1) — plus the stronger
      cross-check that kind and spans equal the oracle's reference computation.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.evid_norm (imported inside each test);
      tests.tools.mem01_verify.reference (the §6 reference, proven by test_oracle_helpers.py).
Key invariants:
  - Seeded `random.Random` (FIRST: repeatable) — no property-testing dependency was added.
  - The generator draws from the scalars §6 names explicitly plus every Unicode White_Space
    scalar (§16.10), so the property covers the whole whitespace rule.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import InstrumentLoader

SEED = 20260906
TEXT_CASES = 400
CUTS_PER_TEXT = 3

BG_WORDS = ("Здравей", "свят", "фактура", "договор", "плащане", "срок", "кирилица", "Приложение")
EN_WORDS = ("hello", "world", "invoice", "contract", "payment", "deadline", "Re:", "Fwd:")
ASCII_PUNCT = ('"', "'", "-", ",", ".", "...", ":", ";", "(", ")")
TYPOGRAPHIC = tuple(
    sorted(reference.DOUBLE_QUOTES | reference.SINGLE_QUOTES | reference.DASHES)
) + (reference.ELLIPSIS,)
SPACES = (" ", "  ", "\t", "\r\n", "\n", " \r\n ", "\t\t") + tuple(
    sorted(reference.MAPPED_SPACES | reference.UNICODE_WHITE_SPACE)
)
ZERO_WIDTH = tuple(sorted(reference.REMOVED))
EMOJI = ("😀", "👍🏽", "👨" + chr(0x200D) + "👩" + chr(0x200D) + "👧", "🇧🇬")
COMBINING = ("и" + chr(0x0306), "e" + chr(0x0301), "a" + chr(0x0308), "Й")


@dataclass(frozen=True)
class RoundTripCase:
    """One generated text and one substring cut from it whose normalization is non-empty."""

    seed_index: int
    text: str
    start: int
    end: int

    @property
    def quote(self) -> str:
        return self.text[self.start : self.end]


def _generate_text(rng: random.Random) -> str:
    pieces: list[str] = []
    for _ in range(rng.randint(3, 40)):
        roll = rng.random()
        if roll < 0.25:
            pieces.append(rng.choice(BG_WORDS))
        elif roll < 0.45:
            pieces.append(rng.choice(EN_WORDS))
        elif roll < 0.62:
            pieces.append(rng.choice(SPACES))
        elif roll < 0.72:
            pieces.append(rng.choice(TYPOGRAPHIC))
        elif roll < 0.80:
            pieces.append(rng.choice(ASCII_PUNCT))
        elif roll < 0.86:
            pieces.append(rng.choice(ZERO_WIDTH))
        elif roll < 0.91:
            pieces.append(rng.choice(EMOJI))
        elif roll < 0.96:
            pieces.append(rng.choice(COMBINING))
        elif pieces:
            repeat_from = rng.randrange(len(pieces))
            pieces.extend(pieces[repeat_from : repeat_from + rng.randint(1, 4)])
        else:
            pieces.append(" ")
    return "".join(pieces)


def generate_cases(
    seed: int = SEED, texts: int = TEXT_CASES, cuts: int = CUTS_PER_TEXT
) -> list[RoundTripCase]:
    """Deterministic generator: `texts` texts, up to `cuts` non-empty substrings each."""
    rng = random.Random(seed)
    cases: list[RoundTripCase] = []
    index = 0
    while len(cases) < texts * cuts and index < texts * 4:
        index += 1
        text = _generate_text(rng)
        if not reference.normalize_reference(text).text:
            continue
        made = 0
        attempts = 0
        while made < cuts and attempts < 20:
            attempts += 1
            start = rng.randrange(len(text))
            end = rng.randint(start + 1, len(text))
            if reference.normalize_reference(text[start:end]).text:
                cases.append(RoundTripCase(index, text, start, end))
                made += 1
    return cases


def test_generator_is_seeded_and_covers_every_scalar_family() -> None:
    cases = generate_cases()
    corpus = "".join(case.text for case in cases)

    assert cases == generate_cases()
    assert len(cases) >= TEXT_CASES * 2
    assert any(char in corpus for char in reference.REMOVED)
    assert any(char in corpus for char in reference.MAPPED_SPACES)
    assert reference.ELLIPSIS in corpus and "\r\n" in corpus
    assert chr(0x0301) in corpus and "😀" in corpus and "Здравей" in corpus


def test_reference_itself_satisfies_the_property_on_every_generated_case() -> None:
    """Helper proof (brief §5 b): reference and generator are coherent; passes today."""
    failures: list[str] = []
    for case in generate_cases():
        kind, intervals = reference.resolve_reference(case.quote, case.text)
        trimmed = reference.trim_edges(case.quote)
        quote_norm = reference.normalize_reference(case.quote).text
        if kind not in ("resolved", "ambiguous"):
            failures.append(f"{case.seed_index}: kind {kind}")
        elif not any(reference.trim_edges(case.text[a:b]) == trimmed for a, b in intervals):
            failures.append(f"{case.seed_index}: no interval trims to the quote")
        elif any(
            reference.normalize_reference(case.text[a:b]).text != quote_norm for a, b in intervals
        ):
            failures.append(f"{case.seed_index}: an interval normalizes differently")
    assert not failures, "\n".join(failures[:20])


def test_round_trip_property_holds_on_every_generated_case(instrument: InstrumentLoader) -> None:
    evid_norm = instrument("evid_norm")
    failures: list[str] = []
    for case in generate_cases():
        text, quote = case.text, case.quote
        resolution = evid_norm.resolve(quote, text)
        expected_kind, expected_intervals = reference.resolve_reference(quote, text)
        spans = {(span.scalar_start, span.scalar_end) for span in resolution.spans}
        quote_norm = evid_norm.normalize(quote).text
        problems: list[str] = []
        if resolution.kind not in ("resolved", "ambiguous"):
            problems.append(f"(i) kind={resolution.kind}")
        trimmed_quote = reference.trim_edges(quote)
        if not any(reference.trim_edges(text[a:b]) == trimmed_quote for a, b in spans):
            problems.append("(ii) no span trims to the quote")
        for a, b in spans:
            if evid_norm.normalize(text[a:b]).text != quote_norm:
                problems.append(f"(iii) span {a, b} normalizes differently")
        for span in resolution.spans:
            expected_bytes = reference.utf8_byte_offsets_reference(
                text, span.scalar_start, span.scalar_end
            )
            if (span.byte_start, span.byte_end) != expected_bytes:
                problems.append(
                    f"byte offsets {span.byte_start, span.byte_end} != {expected_bytes}"
                )
        if resolution.kind != expected_kind or spans != set(expected_intervals):
            problems.append(f"reference disagrees: {expected_kind} {sorted(expected_intervals)}")
        if problems:
            failures.append(
                f"case {case.seed_index} [{case.start}:{case.end}] {ascii(text)}: "
                + "; ".join(problems)
            )
    assert not failures, "\n".join(failures[:20]) + f"\n... {len(failures)} failing cases"


def test_offset_map_property_holds_on_every_generated_text(instrument: InstrumentLoader) -> None:
    evid_norm = instrument("evid_norm")
    failures: list[str] = []
    seen: set[str] = set()
    for case in generate_cases():
        text = case.text
        if text in seen:
            continue
        seen.add(text)
        normalized = evid_norm.normalize(text)
        expected = reference.normalize_reference(text)
        problems: list[str] = []
        if normalized.text != expected.text:
            problems.append("normalized text differs from the reference")
        if list(normalized.source_positions) != sorted(normalized.source_positions):
            problems.append("(iv) source_positions not non-decreasing")
        if len(normalized.source_positions) != len(normalized.text):
            problems.append("(iv) source_positions length != text length")
        if tuple(normalized.source_positions) != expected.source_positions:
            problems.append("source_positions differ from the reference")
        if frozenset(normalized.unit_starts) != expected.unit_starts:
            problems.append("unit_starts differ from the reference")
        first_kept, last_kept_end = expected.unit_spans[0][1], expected.unit_spans[-1][2]
        if normalized.to_original(0, len(normalized.text)) != (first_kept, last_kept_end):
            problems.append("(iv) to_original of the full text != [first_kept, last_kept + 1)")
        if normalized.original_len != len(text):
            problems.append("original_len differs")
        if evid_norm.normalize(normalized.text).text != normalized.text:
            problems.append("normalization is not idempotent")
        if problems:
            failures.append(f"text {ascii(text)}: " + "; ".join(problems))
    assert not failures, "\n".join(failures[:20]) + f"\n... {len(failures)} failing texts"
