"""Public surface of the MEM-01 IDEM (idempotence) conformance fixture battery.

Role:
    The one import site the IDEM gate evaluator and the fixture digest use. Re-exports
    `IDEM_SCENARIOS` and the battery's record types from the two data modules the battery is
    split across (contract 1.3 names `fixtures.idem_scenarios.IDEM_SCENARIOS`; the split into
    `_a`/`_b` exists only to keep every file under the house line ceiling).
Used by:
    `tools.mem01_verify.gates.gate_idem`, `tools.mem01_verify.fixtures.digest`, and the
    instrument tests under `backend/tests/tools/mem01_verify/fixtures/`.
Depends on:
    `tools.mem01_verify.fixtures.idem_scenarios_a` (record types and the `.eml` builders),
    `…_b` (the synthetic originals and the delta constructors), `…_c` (the scenarios whose
    primary criterion is `idem.replay_no_change`) and `…_d` (the exactly-once scenarios, the
    stage-C backfill shape, and the assembled `IDEM_SCENARIOS`).
Key invariants:
    - This module adds no data and no logic: it only re-exports, so the battery has exactly
      one definition of every record.
    - `IDEM_SCENARIOS` case ids are unique and every `criterion_id` / entry of `also_pins` is
      an id declared under the `IDEM` gate of `release/criteria.step1.v1.yaml`.
    - Contract 10.6 floors: at least ten scenarios, and at least ten of them contribute to
      each of `idem.replay_no_change` and `idem.exactly_once_committed` (the criteria file
      sets `minimum: 10` on both).
"""

from __future__ import annotations

from tools.mem01_verify.fixtures.idem_scenarios_a import (
    CRLF,
    TRACKED_TABLES,
    AttachmentSpec,
    EmlSpec,
    IdemScenario,
    RowDelta,
    ScenarioExpectation,
    ScenarioStep,
    StepAction,
    build_eml_bytes,
    build_scenario_payloads,
)
from tools.mem01_verify.fixtures.idem_scenarios_b import (
    BACKFILL_CRITERION,
    EXACTLY_ONCE_CRITERION,
    REPLAY_CRITERION,
)
from tools.mem01_verify.fixtures.idem_scenarios_d import IDEM_SCENARIOS

__all__ = [
    "BACKFILL_CRITERION",
    "CRLF",
    "EXACTLY_ONCE_CRITERION",
    "IDEM_SCENARIOS",
    "REPLAY_CRITERION",
    "TRACKED_TABLES",
    "AttachmentSpec",
    "EmlSpec",
    "IdemScenario",
    "RowDelta",
    "ScenarioExpectation",
    "ScenarioStep",
    "StepAction",
    "build_eml_bytes",
    "build_scenario_payloads",
]
