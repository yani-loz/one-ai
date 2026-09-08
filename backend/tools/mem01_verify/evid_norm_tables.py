"""
Role: The frozen character tables of EVID_NORM_V1 (contract §6 and §16.10) — the typographic
      quote / dash / ellipsis mappings, the removed zero-width scalars, the mapped space-class
      scalars and the full Unicode `White_Space` set. The tables ARE part of the version: they
      are literal constants, never derived at runtime from `str.isspace()` or `unicodedata`
      (both drift with the CPython Unicode database and `str.isspace()` is additionally wrong —
      it is true for U+001C..U+001F, which do not carry the `White_Space` property).
Used by: tools.mem01_verify.evid_norm.
Depends on: nothing inside the project (stdlib `types` only, for the read-only mapping).
Key invariants:
  - Every scalar is spelled as an escape so the table is reviewable character by character.
  - The four mapping classes (DOUBLE_QUOTES, SINGLE_QUOTES, DASHES, ELLIPSIS), REMOVED and
    WHITESPACE are pairwise disjoint; a scalar therefore has exactly one fate.
  - Changing any table changes EVID_NORM_VERSION; it is never edited in place.
"""

from __future__ import annotations

from types import MappingProxyType

# „ “ ” « » ‟ ″  → '"'
DOUBLE_QUOTES: frozenset[str] = frozenset("\u201e\u201c\u201d\u00ab\u00bb\u201f\u2033")

# ‘ ’ ‚ ‹ › ′ ʼ  → "'"
SINGLE_QUOTES: frozenset[str] = frozenset("\u2018\u2019\u201a\u2039\u203a\u2032\u02bc")

# ‐ ‑ ‒ – — ― −  → '-'
DASHES: frozenset[str] = frozenset("\u2010\u2011\u2012\u2013\u2014\u2015\u2212")

# …  → '...' (ONE unit of length three)
ELLIPSIS: str = "\u2026"

# zero-width space / joiner / non-joiner / BOM / soft hyphen → removed (no unit)
REMOVED: frozenset[str] = frozenset("\u200b\u200d\u200c\ufeff\u00ad")

# NBSP, narrow NBSP, thin, hair, en, em, figure, punctuation and ideographic space → ' '
MAPPED_SPACES: frozenset[str] = frozenset("\u00a0\u202f\u2009\u200a\u2002\u2003\u2007\u2008\u3000")

ASCII_WHITESPACE: frozenset[str] = frozenset(" \t\n\r\x0b\x0c")

# Every scalar carrying the Unicode `White_Space` property (contract §16.10), transcribed:
# U+0009..U+000D, U+0020, U+0085, U+00A0, U+1680, U+2000..U+200A, U+2028, U+2029,
# U+202F, U+205F, U+3000.
UNICODE_WHITE_SPACE: frozenset[str] = frozenset(
    "\t\n\x0b\x0c\r \x85\xa0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)

# The §6 whitespace class: a run of these collapses to exactly one ASCII space.
WHITESPACE: frozenset[str] = ASCII_WHITESPACE | MAPPED_SPACES | UNICODE_WHITE_SPACE

# Frozen single-scalar expansions applied before the whitespace rule.
SCALAR_EXPANSIONS: MappingProxyType[str, str] = MappingProxyType(
    {
        **{char: '"' for char in DOUBLE_QUOTES},
        **{char: "'" for char in SINGLE_QUOTES},
        **{char: "-" for char in DASHES},
        ELLIPSIS: "...",
    }
)
