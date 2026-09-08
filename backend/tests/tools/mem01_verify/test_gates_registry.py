"""
Role: Seals the gate registry of contract §1.3/§1.4 — exactly 17 gate names in the frozen
      order, the H-split and holdout tuples, one evaluator module per gate, the context types,
      and agreement between the registry and the criteria annex.
Used by: the seal review.
Depends on: tools.mem01_verify.gates.registry, .gates.context, .gates.gate_<name> (imported
      inside each test); the criteria annex through pyyaml.
Key invariants:
  - The expected tuple is spelled out here by hand from §1.3, never read from the instrument.
"""

from __future__ import annotations

import dataclasses

import pytest

from tests.tools.mem01_verify.conftest import GATE_NAMES, InstrumentLoader

EXPECTED_GATE_NAMES = (
    "QS",
    "CH",
    "NF",
    "LANG",
    "IDEM",
    "VIS",
    "ERASE",
    "RET",
    "COV",
    "FID",
    "THR",
    "TIME",
    "IDENT",
    "RED",
    "ATTR",
    "SNAP",
    "EMB",
)
GATE_CONTEXT_FIELDS = {
    "release",
    "criteria",
    "run_kind",
    "split_evaluated",
    "org_id",
    "corpus",
    "corpus_snapshot",
    "probe",
    "fixtures_digest",
    "report_dir",
    "hidden_root",
    "versions",
}
GATE_RESULT_FIELDS = {"name", "status", "reason", "criteria", "diagnostics", "report_files"}


def test_gate_names_are_exactly_the_seventeen_in_frozen_order(instrument: InstrumentLoader) -> None:
    registry = instrument("gates.registry")

    assert registry.GATE_NAMES == EXPECTED_GATE_NAMES
    assert len(set(registry.GATE_NAMES)) == 17
    assert registry.GATE_NAMES == GATE_NAMES


def test_h_split_and_holdout_tuples(instrument: InstrumentLoader) -> None:
    registry = instrument("gates.registry")

    assert registry.H_SPLIT_GATES == ("QS", "NF", "LANG", "RET")
    assert registry.HOLDOUT_GATES == ("FID", "THR", "IDENT", "ATTR")


def test_registry_agrees_with_the_criteria_annex(
    instrument: InstrumentLoader, criteria_yaml: dict
) -> None:
    registry = instrument("gates.registry")

    assert set(registry.GATE_NAMES) == set(criteria_yaml["gates"])
    assert list(registry.HOLDOUT_GATES) == list(criteria_yaml["provisional_gates"])


@pytest.mark.parametrize("gate", EXPECTED_GATE_NAMES)
def test_every_gate_has_an_evaluator_module(instrument: InstrumentLoader, gate: str) -> None:
    module = instrument(f"gates.gate_{gate.lower()}")

    assert callable(module.evaluate)


def test_context_and_result_types_carry_the_contract_fields_and_are_frozen(
    instrument: InstrumentLoader,
) -> None:
    context = instrument("gates.context")

    context_fields = {field.name for field in dataclasses.fields(context.GateContext)}
    result_fields = {field.name for field in dataclasses.fields(context.GateResult)}

    assert GATE_CONTEXT_FIELDS <= context_fields
    assert GATE_RESULT_FIELDS <= result_fields
    assert context.GateContext.__dataclass_params__.frozen  # type: ignore[attr-defined]
    assert context.GateResult.__dataclass_params__.frozen  # type: ignore[attr-defined]


def test_evaluate_all_is_exposed(instrument: InstrumentLoader) -> None:
    registry = instrument("gates.registry")

    assert callable(registry.evaluate_all)


@pytest.mark.parametrize("gate", ("qs", "nf", "lang", "ret"))
def test_h_split_gate_reports_no_hidden_scorability_in_stage_a(
    instrument: InstrumentLoader, gate: str
) -> None:
    module = instrument(f"gates.gate_{gate}")

    assert callable(module.hidden_scorable)
    assert module.hidden_scorable() is False  # §16.13: no measured component, no labels yet
