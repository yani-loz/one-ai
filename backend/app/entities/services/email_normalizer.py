"""
Role: The canonical email match-key normalizer — the SINGLE source of truth for turning a raw,
      as-seen address into the deterministic key used to get-or-create a Person. Deliberately
      conservative: case-fold + trim only, NO Gmail-dot or plus-subaddress canonicalization.
Used by: app.entities.services.entity_resolver (applied on BOTH the lookup and the insert path —
         a mismatch between the two silently fails to match, so there is exactly one normalizer).
Depends on: stdlib only.
Key invariants:
  - normalize_email is `strip().lower()` — and `.lower()`, never `.casefold()` (casefold maps ß→ss,
    which would merge two DISTINCT addresses). Provider-specific canonicalization (Gmail dots, '+'
    subaddressing) is intentionally OUT: it is domain-specific and over-merge-prone, and over-merge
    pollutes a whole dossier whereas under-merge only fragments (an accepted, recoverable limit —
    design §5/§6). The raw as-seen address is retained on the source rows, so a smarter, provider-
    aware tier can get more aggressive later from stored data with no re-fetch.
  - extract_domain returns the lowercased host after the LAST '@', or None — used for company
    resolution and the internal/external decision.
"""

from __future__ import annotations


def normalize_email(raw_address: str) -> str:
    """Normalize a raw email address to its deterministic match key (trim + lowercase only).

    Args:
        raw_address: an address as seen on the wire (already a bare addr-spec from the parser, but
            stray surrounding angle brackets/whitespace are tolerated defensively).

    Returns:
        The lowercased, trimmed address. NOT validated here — an empty/garbage input returns its
        trimmed-lowered form and the caller decides whether to use it.
    """
    return raw_address.strip().strip("<>").strip().lower()


def extract_domain(normalized_address: str) -> str | None:
    """Return the domain (lowercased host after the last '@') of a normalized address, or None."""
    if "@" not in normalized_address:
        return None
    domain = normalized_address.rsplit("@", 1)[1].strip()
    return domain or None
