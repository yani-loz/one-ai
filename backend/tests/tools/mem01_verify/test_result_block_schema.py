"""
Role: Seals the schema validation of contract §3.3/§3.4 — `validate_result_block` accepts every
      schema-complete run kind and the aborted shape, and rejects each enumerated violation
      (listing all of them in one error).
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.result_block and .exceptions (imported inside each test);
      tests.tools.mem01_verify.result_block_samples.
Key invariants:
  - Each rejection first proves the unmutated deep copy validates (the positive control).
"""

from __future__ import annotations

import copy

import pytest

from tests.tools.mem01_verify.conftest import InstrumentLoader
from tests.tools.mem01_verify.result_block_samples import (
    HEX,
    aborted_block,
    completed_block,
    make_all_pass,
)


@pytest.mark.parametrize("run_kind", ["tuning", "checkpoint", "validation"])
def test_validate_accepts_a_schema_complete_block_of_each_run_kind(
    instrument: InstrumentLoader, run_kind: str
) -> None:
    result_block = instrument("result_block")

    result_block.validate_result_block(completed_block(run_kind), projection="protected")


def test_validate_accepts_an_all_pass_block_without_reason(instrument: InstrumentLoader) -> None:
    result_block = instrument("result_block")

    result_block.validate_result_block(
        make_all_pass(completed_block(), keep_reason=False), projection="protected"
    )


def test_validate_accepts_an_aborted_block(instrument: InstrumentLoader) -> None:
    result_block = instrument("result_block")

    result_block.validate_result_block(aborted_block(), projection="protected")


def _mutations() -> dict[str, tuple[str, object]]:
    """name → (run_kind, mutator) where mutator edits a valid block in place."""

    def drop(key: str):
        return lambda b: b.pop(key)

    def set_(key: str, value: object):
        return lambda b: b.__setitem__(key, value)

    return {
        "missing_schema": ("tuning", drop("schema")),
        "wrong_phase": ("tuning", set_("phase", "step2")),
        "reason_present_on_pass": ("tuning", lambda b: make_all_pass(b, keep_reason=True)),
        "reason_absent_on_error": ("tuning", drop("reason")),
        "hidden_budget_on_tuning": ("tuning", set_("hidden_budget", "1/20")),
        "hidden_budget_missing_on_checkpoint": ("checkpoint", drop("hidden_budget")),
        "validation_on_tuning": ("tuning", set_("validation", "complete")),
        "validation_missing_on_validation": ("validation", drop("validation")),
        "provisional_wrong_order": (
            "tuning",
            set_("provisional_gates", ["THR", "FID", "IDENT", "ATTR"]),
        ),
        "sets_missing_emb": ("tuning", lambda b: b["sets"].pop("EMB")),
        "gates_unknown_gate": (
            "tuning",
            lambda b: b["gates"].__setitem__("FOO", {"status": "PASS", "criteria": []}),
        ),
        "gate_status_invalid": (
            "tuning",
            lambda b: b["gates"]["SNAP"].__setitem__("status", "maybe"),
        ),
        "gate_non_pass_without_reason": ("tuning", lambda b: b["gates"]["QS"].pop("reason")),
        "criteria_entry_bad_split": (
            "tuning",
            lambda b: b["criteria"][0].__setitem__("split", "hidden"),
        ),
        "criteria_entry_missing_kind": ("tuning", lambda b: b["criteria"][0].pop("kind")),
        "criteria_entry_bad_status": (
            "tuning",
            lambda b: b["criteria"][0].__setitem__("status", "OK"),
        ),
        "cache_policy_allowed": ("tuning", set_("cache_policy", "allowed")),
        "cache_hits_nonzero": ("tuning", set_("cache_hits", 1)),
        "hash_63_chars": ("tuning", set_("code_hash", HEX[:63])),
        "hash_uppercase": ("tuning", set_("config_hash", HEX.upper())),
        "partial_not_bool": ("tuning", set_("partial", "no")),
        "versions_missing_tnefparse": ("tuning", lambda b: b["versions"].pop("tnefparse")),
        "cleanup_missing": ("tuning", drop("cleanup")),
        "corpus_missing_snapshot_txid": (
            "tuning",
            lambda b: b["corpus"].pop("snapshot_transaction_id"),
        ),
        "aborted_true_on_completed_shape": ("tuning", set_("aborted", True)),
        "split_evaluated_wrong_for_checkpoint": (
            "checkpoint",
            set_("split_evaluated", "optimization"),
        ),
    }


@pytest.mark.parametrize("name", sorted(_mutations()))
def test_validate_rejects_each_schema_violation(instrument: InstrumentLoader, name: str) -> None:
    result_block = instrument("result_block")
    exceptions = instrument("exceptions")
    run_kind, mutate = _mutations()[name]
    block = completed_block(run_kind)
    result_block.validate_result_block(copy.deepcopy(block), projection="protected")  # control
    mutate(block)

    with pytest.raises(exceptions.ResultBlockError):
        result_block.validate_result_block(block, projection="protected")


def test_validate_lists_every_violation_in_one_error(instrument: InstrumentLoader) -> None:
    result_block = instrument("result_block")
    exceptions = instrument("exceptions")
    block = completed_block()
    block.pop("cleanup")
    block["cache_hits"] = 7

    with pytest.raises(exceptions.ResultBlockError) as caught:
        result_block.validate_result_block(block, projection="protected")

    message = str(caught.value)
    assert "cleanup" in message and "cache_hits" in message


def test_aborted_block_requires_aborted_at_step(instrument: InstrumentLoader) -> None:
    result_block = instrument("result_block")
    exceptions = instrument("exceptions")
    block = aborted_block()
    block.pop("aborted_at_step")

    with pytest.raises(exceptions.ResultBlockError):
        result_block.validate_result_block(block, projection="protected")
