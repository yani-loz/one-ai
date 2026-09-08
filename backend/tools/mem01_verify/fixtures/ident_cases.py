"""IDENT fixture battery — the public surface named in Stage A contract §1.3.

Role: the single import point for the IDENT battery. It re-exports the three tuples the contract
    names (``ALIAS_PAIRS``, ``DISTINCT_PAIRS``, ``STABILITY_CONTROLS``) together with their record
    types and the three F criterion ids they answer to. The records themselves are split across
    five sibling modules — ``ident_cases_a`` (record types, criterion ids, alias pairs part 1),
    ``ident_cases_c`` (alias pairs part 2), ``ident_cases_d`` (stability controls),
    ``ident_cases_b`` (the distinct-pair type and part 1) and ``ident_cases_e`` (distinct pairs
    part 2) — only to stay under the file-size ceiling of `.claude/rules/code-quality.md` A2.
Used by: the IDENT gate evaluator, ``fixtures.digest.fixtures_digest`` (the battery digest enters
    ``config_hash``), and any oracle test targeting ``fixtures.ident_cases``.
Depends on: ``tools.mem01_verify.fixtures.ident_cases_a``, ``…_b``, ``…_c``, ``…_d`` and ``…_e`` —
    data only.
Key invariants:
    - The split is an implementation detail; this module's names are the contract. Adding a record
      to any part changes the battery digest, so it changes ``config_hash`` — intended.
    - Concatenation order is frozen: ``ALIAS_PAIRS`` is part A then part C, ``DISTINCT_PAIRS`` is
      part B then part E. That is the order the records were authored in, so a record moving
      between parts — or the parts being concatenated the other way round — would change the
      battery digest even though no record changed.
    - Case ids are unique across the whole battery: ``ident-0NN`` alias pairs, ``ident-1NN``
      must-remain-distinct pairs, ``ident-2NN`` stability controls.
    - Denominator floors from ``criteria.step1.v1.yaml``: alias_resolution >= 20, no_false_merge
      >= 40, exact_address_stability >= 10. Below a floor the criterion is ERROR, never PASS, so
      records are only ever added here, never removed.
    - Expectations are authored independently of every measured component (contract R12).
"""

from __future__ import annotations

from tools.mem01_verify.fixtures.ident_cases_a import (
    ALIAS_PAIRS_A,
    ALIAS_RESOLUTION,
    EXACT_ADDRESS_STABILITY,
    NO_FALSE_MERGE,
    AliasPair,
    AliasProvenance,
    AliasSourceKind,
)
from tools.mem01_verify.fixtures.ident_cases_b import (
    DISTINCT_PAIRS_B,
    DistinctCategory,
    DistinctPair,
)
from tools.mem01_verify.fixtures.ident_cases_c import ALIAS_PAIRS_C
from tools.mem01_verify.fixtures.ident_cases_d import STABILITY_CONTROLS, StabilityControl
from tools.mem01_verify.fixtures.ident_cases_e import DISTINCT_PAIRS_E

ALIAS_PAIRS: tuple[AliasPair, ...] = ALIAS_PAIRS_A + ALIAS_PAIRS_C
DISTINCT_PAIRS: tuple[DistinctPair, ...] = DISTINCT_PAIRS_B + DISTINCT_PAIRS_E

__all__ = [
    "ALIAS_PAIRS",
    "ALIAS_RESOLUTION",
    "DISTINCT_PAIRS",
    "EXACT_ADDRESS_STABILITY",
    "NO_FALSE_MERGE",
    "STABILITY_CONTROLS",
    "AliasPair",
    "AliasProvenance",
    "AliasSourceKind",
    "DistinctCategory",
    "DistinctPair",
    "StabilityControl",
]
