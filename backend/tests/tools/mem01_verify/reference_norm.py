"""
Role: Independent reference implementation of contract §6 EVID_NORM_V1 — the frozen mapping
      table, the unit model, the offset map and unit-aligned resolution — plus the §16.10
      White_Space rule, kept apart from reference.py so both stay under the house size target.
Used by: reference.py (re-exports every public name), test_evid_norm.py,
      test_evid_norm_property.py, test_oracle_helpers.py.
Depends on: stdlib only. Never imports tools.mem01_verify or app.*.
Key invariants:
  - The tables are transcribed from contract §6 / §16.10 character by character; they are the
    oracle's definition of the rule, not a copy of any implementation.
  - `WHITESPACE` = every Unicode White_Space scalar plus the §6 space-class scalars (§16.10);
    zero-width scalars are REMOVED, never whitespace.
"""

from __future__ import annotations

from dataclasses import dataclass

# Code points spelled out as escapes so the table is reviewable (contract 6 lists glyphs).
DOUBLE_QUOTES = frozenset("\u201e\u201c\u201d\u00ab\u00bb\u201f\u2033")  # „ “ ” « » ‟ ″
SINGLE_QUOTES = frozenset("\u2018\u2019\u201a\u2039\u203a\u2032\u02bc")  # ‘ ’ ‚ ‹ › ′ ʼ
DASHES = frozenset("\u2010\u2011\u2012\u2013\u2014\u2015\u2212")  # ‐ ‑ ‒ – — ― −
ELLIPSIS = "\u2026"  # …
REMOVED = frozenset("\u200b\u200d\u200c\ufeff\u00ad")  # ZWSP ZWJ ZWNJ BOM soft hyphen
MAPPED_SPACES = frozenset(
    "\u00a0\u202f\u2009\u200a\u2002\u2003\u2007\u2008\u3000"
)  # NBSP, narrow NBSP, thin, hair, en, em, figure, punctuation, ideographic
ASCII_WHITESPACE = frozenset(" \t\n\r\x0b\x0c")
UNICODE_WHITE_SPACE = frozenset(
    "\t\n\x0b\x0c\r \x85\xa0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)  # every scalar with the Unicode White_Space property (contract 16.10)
WHITESPACE = ASCII_WHITESPACE | MAPPED_SPACES | UNICODE_WHITE_SPACE


@dataclass(frozen=True)
class NormalizedReference:
    """The oracle's view of a normalization: text, offset map, unit boundaries, unit spans."""

    text: str
    source_positions: tuple[int, ...]
    unit_starts: frozenset[int]
    unit_spans: tuple[tuple[int, int, int], ...]  # (normalized_start, source_start, source_end)
    original_len: int


def normalize_reference(text: str) -> NormalizedReference:
    """Apply the §6 pipeline: map, remove, collapse whitespace runs, strip; one unit per scalar."""
    units: list[tuple[str, int, int]] = []
    index, length = 0, len(text)
    while index < length:
        char = text[index]
        if char in REMOVED:
            index += 1
            continue
        if char in WHITESPACE:
            run_start, last_space, cursor = index, index, index + 1
            while cursor < length and (text[cursor] in WHITESPACE or text[cursor] in REMOVED):
                if text[cursor] in WHITESPACE:
                    last_space = cursor
                cursor += 1
            units.append((" ", run_start, last_space + 1))
            index = cursor
            continue
        if char in DOUBLE_QUOTES:
            piece = '"'
        elif char in SINGLE_QUOTES:
            piece = "'"
        elif char in DASHES:
            piece = "-"
        elif char == ELLIPSIS:
            piece = "..."
        else:
            piece = char
        units.append((piece, index, index + 1))
        index += 1
    while units and units[0][0] == " ":
        units.pop(0)
    while units and units[-1][0] == " ":
        units.pop()
    pieces: list[str] = []
    positions: list[int] = []
    starts: list[int] = []
    spans: list[tuple[int, int, int]] = []
    cursor = 0
    for piece, source_start, source_end in units:
        starts.append(cursor)
        spans.append((cursor, source_start, source_end))
        pieces.append(piece)
        positions.extend([source_start] * len(piece))
        cursor += len(piece)
    return NormalizedReference(
        text="".join(pieces),
        source_positions=tuple(positions),
        unit_starts=frozenset(starts),
        unit_spans=tuple(spans),
        original_len=length,
    )


def to_original_reference(normalized: NormalizedReference, start: int, end: int) -> tuple[int, int]:
    """§6 offset map: minimal original span covering the units of normalized [start, end)."""
    text_len = len(normalized.text)
    if start not in normalized.unit_starts or (
        end != text_len and end not in normalized.unit_starts
    ):
        raise ValueError(f"not a unit boundary: [{start}, {end})")
    if start >= end:
        raise ValueError("empty range")
    covered = [span for span in normalized.unit_spans if start <= span[0] < end]
    return covered[0][1], covered[-1][2]


def resolve_reference(quote: str, artifact: str) -> tuple[str, frozenset[tuple[int, int]]]:
    """§6 resolution: unit-aligned occurrences mapped to distinct original intervals."""
    quote_norm = normalize_reference(quote)
    artifact_norm = normalize_reference(artifact)
    if not quote_norm.text:
        return "unresolved", frozenset()
    intervals: set[tuple[int, int]] = set()
    needle, haystack = quote_norm.text, artifact_norm.text
    for position in range(len(haystack) - len(needle) + 1):
        if not haystack.startswith(needle, position):
            continue
        end = position + len(needle)
        if position not in artifact_norm.unit_starts:
            continue
        if end != len(haystack) and end not in artifact_norm.unit_starts:
            continue
        intervals.add(to_original_reference(artifact_norm, position, end))
    if not intervals:
        return "unresolved", frozenset()
    if len(intervals) == 1:
        return "resolved", frozenset(intervals)
    return "ambiguous", frozenset(intervals)


def trim_edges(text: str) -> str:
    """Drop leading/trailing scalars that normalize to nothing or to whitespace (§6 property ii)."""
    edge = REMOVED | WHITESPACE
    start, end = 0, len(text)
    while start < end and text[start] in edge:
        start += 1
    while end > start and text[end - 1] in edge:
        end -= 1
    return text[start:end]


def utf8_byte_offsets_reference(text: str, start: int, end: int) -> tuple[int, int]:
    """UTF-8 byte offsets of scalar positions `start`/`end` inside `text`."""
    return len(text[:start].encode("utf-8")), len(text[:end].encode("utf-8"))
