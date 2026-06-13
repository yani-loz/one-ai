"""
Role: The ONE TNEF compressed-RTF-body flatten primitive + its LZFu decompression-bomb bound,
      consolidated from the logic that was DUPLICATED between dedup_key.py (the interior digest's
      RTF component) and extractors/tnef.py (the body cascade's RTF layer). Takes an
      ALREADY-PARSED tnefparse TNEF container (so it never imports tnefparse itself — connector- and
      parser-agnostic) and flattens its compressed-RTF body to text, gating decompression on the
      compressed-property size BEFORE any LZFu work.
Used by: app.connectors.extraction.tnef (the extractor's RTF body layer) and the IMAP connector's
         dedup_key (the content-identity interior digest). Both re-bind MAX_COMPRESSED_RTF_BYTES
         into their OWN namespace and pass it explicitly so each module's monkeypatch of the bound
         still gates its over-bound branch. The arrow points IN — this leaf imports nothing back.
Depends on: compressed-rtf (MIT — invoked through tnefparse's lazy `rtfbody` property, never
            imported directly here), striprtf (BSD-3-Clause — the markup flatten). NOTHING from any
            specific connector, and NOT tnefparse (the caller hands in the parsed container).
Key invariants:
  - SINGLE-SOURCED bound: MAX_COMPRESSED_RTF_BYTES (the measured 8.0x-expansion LZFu-bomb gate) is
    defined here and re-bound by both readers — the key and the extractor MUST apply the SAME bound
    or their notions of "the body" would drift apart (the KEY-CONSISTENCY invariant). The bound is
    a REQUIRED argument (no module-default), so an omitted-arg caller can never silently bypass a
    caller-side monkeypatch by falling back to this module's value.
  - `container._rtfbody` is tnefparse 1.4.0's PRE-DECOMPRESS handle (the raw MAPI_RTF_COMPRESSED
    property bytes stored at parse time); `container.rtfbody` is the LAZY property that
    LZFu-decompresses on access. The bound is checked on the COMPRESSED handle BEFORE the lazy
    property is ever touched (an unbounded 50MB property would materialize ~400MB).
  - flatten_tnef_rtf_body returns the flattened+stripped text, None when the RTF body is absent
    (the 'no-rtf'/'none' case), and RAISES RtfBodyOverBoundError when over bound — each caller maps
    those three outcomes to its own tokens/notes. Decompress/strip failures PROPAGATE so each
    caller's own except degrades them (dedup_key → 'unparseable'; the extractor → next body layer).
"""

from __future__ import annotations

from striprtf.striprtf import rtf_to_text
from tnefparse import TNEF

# LZFu decompression-bomb bound (measured 8.0x expansion ratio on the corpus): compressed-RTF
# property bytes above this are NEVER decompressed (8MB compressed ⇒ ≤ ~64MB transient at the
# measured ratio). SINGLE-SOURCED here and re-bound by dedup_key.py + extraction/tnef.py: the key
# and the extractor must apply the SAME bound, or the two TNEF readers' notions of "the body"
# would drift apart (the KEY-CONSISTENCY invariant).
MAX_COMPRESSED_RTF_BYTES = 8 * 1024 * 1024


class RtfBodyOverBoundError(Exception):
    """The compressed-RTF property exceeds the caller's bound — never decompressed.

    Raised by flatten_tnef_rtf_body so each caller maps it to its own outcome: dedup_key →
    the fixed 'rtf-over-bound' digest token; the extractor → skip the RTF body layer + the
    'rtf-body-over-bound' detail note. Both degrade the body identically (KEY-CONSISTENCY).
    """


def compressed_rtf_over_bound(container: TNEF, max_compressed_bytes: int) -> bool:
    """True when the container's COMPRESSED RTF property exceeds `max_compressed_bytes`.

    Reads the PRE-DECOMPRESS `_rtfbody` handle only — the bound MUST be checked before the lazy
    `rtfbody` property is touched. `max_compressed_bytes` is passed by the caller (its own,
    possibly-monkeypatched, namespace value). Guarded: an odd container could carry a non-sized
    value — the caller's RTF layer then degrades on its own.
    """
    try:
        compressed_rtf_property = container._rtfbody  # the pre-decompress handle — see module doc
        return compressed_rtf_property is not None and (
            len(compressed_rtf_property) > max_compressed_bytes
        )
    except Exception:
        return False


def flatten_tnef_rtf_body(container: TNEF, max_compressed_bytes: int) -> str | None:
    """Flatten a TNEF container's compressed-RTF body to stripped text (the shared primitive).

    Reads `container._rtfbody` (the pre-decompress handle): absent/falsey → None (the caller's
    'no-rtf'/'none' case); over `max_compressed_bytes` → raise RtfBodyOverBoundError (never
    decompressed — the caller maps it to its own token/note); else LZFu-decompress via the lazy
    `rtfbody` property, read latin-1 (lossless — RTF source is ASCII-shaped by spec; striprtf
    decodes the \\ansicpg hex escapes itself), flatten via striprtf (errors='replace' — a broken
    \\'xx escape must not lose the body) and strip. Decompress/strip failures PROPAGATE — each
    caller's own except degrades them (dedup_key → 'unparseable'; the extractor → next body layer).
    """
    compressed_rtf_property = container._rtfbody  # the pre-decompress handle — see module doc
    if not compressed_rtf_property:
        return None
    if len(compressed_rtf_property) > max_compressed_bytes:
        raise RtfBodyOverBoundError
    rtf_bytes = container.rtfbody  # lazily decompresses — only touched after the bound passed
    rtf_source = rtf_bytes.decode("latin-1") if isinstance(rtf_bytes, bytes) else str(rtf_bytes)
    return rtf_to_text(rtf_source, errors="replace").strip()
