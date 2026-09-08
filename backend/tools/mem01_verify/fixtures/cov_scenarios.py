"""COV conformance battery — the public `COV_SCENARIOS` tuple for criterion `cov.fixtures`.

Role:
    The single public entry point of the COV fixture battery: assembles the excluded-by-property
    and delivered scenarios (`cov_scenarios_a`) with the not-ready scenarios (`cov_scenarios_b`)
    into `COV_SCENARIOS`, and re-exports the record types. Each record states, from the FROZEN
    scope policy alone, which disposition a physical input must receive: `delivered`,
    `explicitly_excluded` (with a reason) or `not_ready`.

Used by:
    `tools.mem01_verify.gates.gate_cov` (criterion `cov.fixtures`, minimum denominator 20),
    `tools.mem01_verify.fixtures.digest.fixtures_digest`, and the draft release manifest.

Depends on:
    `tools.mem01_verify.fixtures.cov_scenarios_a` and `.cov_scenarios_b`, and
    `tools.mem01_verify.exceptions.FixtureError` (wave 1) — data only, no runtime
    dependency on any measured component. The EXPECTATIONS derive from
    `release/criteria.step1.v1.yaml` -> `scope_policy` (v0) and Stage-A contract 4.6 / 10.8,
    never from running an extractor or a parser (contract R12).

Key invariants:
    - `COV_SCENARIOS` has a unique `case_id` per record, every `criterion_id` is `cov.fixtures`,
      and the battery size is at least the criterion's minimum denominator of 20.
    - `expected.reason` is non-None if and only if the disposition is `explicitly_excluded`.
    - `duplicate_of`, when set, names another record in this same battery.
    - The per-field semantics (what `extraction_status`, `text_present`, `folder_policy` and
      `duplicate_of` mean, and the exclusion/delivery clauses) are documented once, in
      `cov_scenarios_a`; this module does not restate or override them.
    - Import-time validation raises `FixtureError` rather than returning a malformed battery: a
      broken fixture set must never be scored.
"""

from __future__ import annotations

from tools.mem01_verify.exceptions import FixtureError
from tools.mem01_verify.fixtures.cov_scenarios_a import (
    COV_FIXTURE_CRITERION,
    EXCLUDED_BY_PROPERTY,
    CovExpectation,
    CovScenario,
    Disposition,
    InputKind,
)
from tools.mem01_verify.fixtures.cov_scenarios_b import DELIVERED, NOT_READY

__all__ = [
    "COV_FIXTURE_CRITERION",
    "COV_SCENARIOS",
    "CovExpectation",
    "CovScenario",
    "Disposition",
    "InputKind",
]

COV_SCENARIOS: tuple[CovScenario, ...] = EXCLUDED_BY_PROPERTY + DELIVERED + NOT_READY

_MINIMUM_SCENARIOS = 20


def _verify_battery_invariants(scenarios: tuple[CovScenario, ...]) -> None:
    """Fail fast at import on a malformed battery (too few, duplicate ids, dangling links).

    Args:
        scenarios: the assembled battery.

    Raises:
        FixtureError: on any violation of this module's key invariants.
    """
    if len(scenarios) < _MINIMUM_SCENARIOS:
        raise FixtureError(f"cov_scenarios: battery below the minimum of {_MINIMUM_SCENARIOS}")
    known_ids = {scenario.case_id for scenario in scenarios}
    if len(known_ids) != len(scenarios):
        raise FixtureError("cov_scenarios: duplicate case_id in COV_SCENARIOS")
    for scenario in scenarios:
        if scenario.criterion_id != COV_FIXTURE_CRITERION:
            raise FixtureError(f"cov_scenarios: {scenario.case_id} has a foreign criterion_id")
        if scenario.duplicate_of is not None and scenario.duplicate_of not in known_ids:
            raise FixtureError(f"cov_scenarios: {scenario.case_id} has an unknown duplicate_of")
        is_excluded = scenario.expected.disposition == "explicitly_excluded"
        if is_excluded != (scenario.expected.reason is not None):
            raise FixtureError(f"cov_scenarios: {scenario.case_id} reason/disposition mismatch")


_verify_battery_invariants(COV_SCENARIOS)
