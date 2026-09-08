"""
Role: EVID_NORM_V1 (contract §6) — the evidence normalizer that finds a quote inside a snapshot
      artifact tolerant of typographic variation and maps the match back to ORIGINAL offsets.
      Normalization FINDS a match; it never measures one. Provides `normalize` (the frozen
      mapping table, the unit model and the offset map), `utf8_byte_offsets` and `resolve`
      (unit-aligned occurrences deduplicated by original interval).
Used by: the SNAP gate and any gate that must cite a quote inside a snapshot artifact
      (`gates.gate_snap`, `gates.gate_qs`), the leakage instrument's body digest
      (`leakage.EmailNode.normalized_body_sha256`), and the sealed oracle.
Depends on: `tools.mem01_verify.evid_norm_tables` (the frozen character tables) and
      `tools.mem01_verify.exceptions` (`NormalizationError`). Nothing else — the module is pure,
      deterministic and database-free.
Key invariants:
  - NO Unicode normalization form is applied: combining sequences stay as stored, so the offset
    map is monotone and `source_positions` is non-decreasing.
  - Every surviving source scalar produces exactly ONE unit; `…` → `...` is one unit of length
    three; a whitespace run is one unit (a single space) sourced at the run's first scalar.
  - `to_original` accepts only unit boundaries and returns the MINIMAL original span covering
    the units it names; a position inside a multi-character expansion raises NormalizationError.
  - Case is preserved and letters are never transliterated (Cyrillic stays Cyrillic).
  - The mapping table is frozen and part of EVID_NORM_VERSION.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from tools.mem01_verify.evid_norm_tables import REMOVED, SCALAR_EXPANSIONS, WHITESPACE
from tools.mem01_verify.exceptions import NormalizationError

EVID_NORM_VERSION = "EVID_NORM_V1"

REASON_EMPTY_QUOTE = "empty_quote"
REASON_NORMALIZES_TO_EMPTY = "normalizes_to_empty"
REASON_NO_OCCURRENCE = "no_unit_aligned_occurrence"
REASON_STARTS_INSIDE_EXPANSION = "occurrence_starts_inside_expansion"
REASON_ENDS_INSIDE_EXPANSION = "occurrence_ends_inside_expansion"

_SPACE = " "


@dataclass(frozen=True)
class Span:
    """One resolved location inside the ORIGINAL artifact, in scalars and in UTF-8 bytes."""

    scalar_start: int
    scalar_end: int
    byte_start: int
    byte_end: int


@dataclass(frozen=True)
class Normalized:
    """The normalized text with its unit model and its map back to original scalar positions.

    Attributes:
        text: The normalized text (§6 mapping table applied, whitespace runs collapsed,
            leading/trailing whitespace stripped).
        source_positions: For each normalized position, the source scalar position of the unit
            that contains it. Non-decreasing; `len(source_positions) == len(text)`.
        unit_starts: The normalized positions at which a unit begins.
        original_len: `len(original_text)`.
        unit_spans: Per unit, `(normalized_start, source_start, source_end)` in unit order.
            An implementation carrier for `to_original`: unit source ENDS are not recoverable
            from the four contract fields (a whitespace run followed by a removed scalar ends
            before the next unit's source start).
    """

    text: str
    source_positions: tuple[int, ...]
    unit_starts: frozenset[int]
    original_len: int
    unit_spans: tuple[tuple[int, int, int], ...] = ()

    def to_original(self, start: int, end: int) -> tuple[int, int]:
        """Map a normalized half-open range to the minimal original span covering its units.

        Contract:
            `start` must be a unit boundary and `end` a unit boundary or `len(self.text)`;
            the returned `[a, b)` covers exactly the source scalars of the units in
            `[start, end)` — for a whitespace-run unit the whole run — and never includes
            leading or trailing scalars that were deleted or trimmed.

        Edge cases:
            A boundary inside a multi-character expansion (e.g. position 2 of `...`), an empty
            or reversed range, or an out-of-range position raises `NormalizationError`. The
            mapping is not a bijection and never claims one.
        """
        self._check_boundaries(start, end)
        covered = [span for span in self.unit_spans if start <= span[0] < end]
        return covered[0][1], covered[-1][2]

    def _check_boundaries(self, start: int, end: int) -> None:
        """Reject any range that is not unit-aligned, non-empty or inside the text."""
        text_len = len(self.text)
        if start >= end:
            raise NormalizationError(f"empty or reversed normalized range [{start}, {end})")
        if start not in self.unit_starts:
            raise NormalizationError(f"normalized position {start} is not a unit boundary")
        if end != text_len and end not in self.unit_starts:
            raise NormalizationError(f"normalized position {end} is not a unit boundary")


@dataclass(frozen=True)
class Resolution:
    """The outcome of locating a quote inside an artifact (§6)."""

    kind: Literal["resolved", "ambiguous", "unresolved"]
    spans: tuple[Span, ...]
    reason: str


@dataclass(frozen=True)
class _Unit:
    """One normalization unit: its emitted piece and the original scalars it came from."""

    piece: str
    source_start: int
    source_end: int


def _collect_whitespace_run(text: str, index: int) -> tuple[_Unit, int]:
    """Consume a whitespace run (removed scalars may sit inside it) into one space unit.

    The unit's source span ends at the LAST whitespace scalar of the run, so a removed scalar
    on the run's trailing edge is never absorbed into the span.
    """
    length = len(text)
    run_start = last_whitespace = index
    cursor = index + 1
    while cursor < length and (text[cursor] in WHITESPACE or text[cursor] in REMOVED):
        if text[cursor] in WHITESPACE:
            last_whitespace = cursor
        cursor += 1
    return _Unit(_SPACE, run_start, last_whitespace + 1), cursor


def _build_units(text: str) -> list[_Unit]:
    """Apply the §6 pipeline scalar by scalar: delete, expand, collapse whitespace runs."""
    units: list[_Unit] = []
    index, length = 0, len(text)
    while index < length:
        char = text[index]
        if char in REMOVED:
            index += 1
            continue
        if char in WHITESPACE:
            unit, index = _collect_whitespace_run(text, index)
            units.append(unit)
            continue
        units.append(_Unit(SCALAR_EXPANSIONS.get(char, char), index, index + 1))
        index += 1
    return units


def _strip_edge_space_units(units: list[_Unit]) -> list[_Unit]:
    """Drop the leading and trailing collapsed-space units (§6 strip)."""
    first, last = 0, len(units)
    while first < last and units[first].piece == _SPACE:
        first += 1
    while last > first and units[last - 1].piece == _SPACE:
        last -= 1
    return units[first:last]


def normalize(text: str) -> Normalized:
    """Normalize `text` under EVID_NORM_V1 and return its text, unit model and offset map.

    Contract:
        Pure and deterministic. Typographic quotes, apostrophes and dashes fold to their ASCII
        forms, the ellipsis expands to three dots, zero-width scalars and the soft hyphen are
        removed, every run of whitespace collapses to one space, and leading/trailing
        whitespace is stripped. Case is preserved, letters are never transliterated and no
        Unicode normalization form is applied.

    Edge cases:
        An empty text, or a text that is entirely whitespace or entirely removed scalars,
        yields `text == ""`, empty `source_positions`/`unit_starts` and `original_len` set to
        the input length.
    """
    units = _strip_edge_space_units(_build_units(text))
    pieces: list[str] = []
    positions: list[int] = []
    starts: list[int] = []
    spans: list[tuple[int, int, int]] = []
    cursor = 0
    for unit in units:
        starts.append(cursor)
        spans.append((cursor, unit.source_start, unit.source_end))
        pieces.append(unit.piece)
        positions.extend([unit.source_start] * len(unit.piece))
        cursor += len(unit.piece)
    return Normalized(
        text="".join(pieces),
        source_positions=tuple(positions),
        unit_starts=frozenset(starts),
        original_len=len(text),
        unit_spans=tuple(spans),
    )


def utf8_byte_offsets(text: str, start: int, end: int) -> tuple[int, int]:
    """Return the UTF-8 byte offsets of the scalar positions `start` and `end` inside `text`.

    Contract:
        `0 <= start <= end <= len(text)`; the result is the byte length of `text[:start]` and
        of `text[:end]` under UTF-8, so Cyrillic (2 bytes) and astral emoji (4 bytes) are
        accounted exactly.

    Edge cases:
        A negative, reversed or out-of-range pair raises `NormalizationError`.
    """
    if start < 0 or end < start or end > len(text):
        raise NormalizationError(f"scalar range [{start}, {end}) is outside the text")
    return len(text[:start].encode("utf-8")), len(text[:end].encode("utf-8"))


def _unit_aligned_intervals(needle: str, artifact: Normalized) -> tuple[list[tuple[int, int]], str]:
    """Find every occurrence of `needle`; return its aligned original intervals and a reason.

    The reason is the misalignment of the FIRST occurrence that failed, or the
    `no_unit_aligned_occurrence` code when the needle does not occur at all. It is meaningful
    only when the interval list is empty.
    """
    haystack = artifact.text
    intervals: list[tuple[int, int]] = []
    misalignment = ""
    position = haystack.find(needle)
    while position != -1:
        end = position + len(needle)
        if position not in artifact.unit_starts:
            misalignment = misalignment or REASON_STARTS_INSIDE_EXPANSION
        elif end != len(haystack) and end not in artifact.unit_starts:
            misalignment = misalignment or REASON_ENDS_INSIDE_EXPANSION
        else:
            intervals.append(artifact.to_original(position, end))
        position = haystack.find(needle, position + 1)
    return intervals, misalignment or REASON_NO_OCCURRENCE


def resolve(quote: str, artifact_text: str) -> Resolution:
    """Locate `quote` inside `artifact_text` and map every match to ORIGINAL offsets.

    Contract:
        Both sides are normalized under EVID_NORM_V1. A match is an occurrence of the
        normalized quote whose start AND end are unit boundaries, so an occurrence that begins
        or ends inside a multi-character expansion is not a match (a lone dot never matches
        inside an ellipsis). Matches are deduplicated by ORIGINAL interval: exactly one
        distinct interval is `resolved`, two or more are `ambiguous` (never a leftmost
        preference), none is `unresolved`. Every span carries scalar and UTF-8 byte offsets
        into the original.

    Edge cases:
        An empty quote is `unresolved` with reason `empty_quote`; a quote that normalizes away
        is `unresolved` with reason `normalizes_to_empty`; `reason` is empty for a resolved or
        ambiguous outcome. Because normalization strips the quote's edges, a returned span
        never ends on a collapsed-whitespace unit.
    """
    if not quote:
        return Resolution(kind="unresolved", spans=(), reason=REASON_EMPTY_QUOTE)
    needle = normalize(quote).text
    if not needle:
        return Resolution(kind="unresolved", spans=(), reason=REASON_NORMALIZES_TO_EMPTY)
    artifact = normalize(artifact_text)
    intervals, reason = _unit_aligned_intervals(needle, artifact)
    if not intervals:
        return Resolution(kind="unresolved", spans=(), reason=reason)
    spans = tuple(
        Span(start, end, *utf8_byte_offsets(artifact_text, start, end))
        for start, end in sorted(set(intervals))
    )
    kind: Literal["resolved", "ambiguous"] = "resolved" if len(spans) == 1 else "ambiguous"
    return Resolution(kind=kind, spans=spans, reason="")
