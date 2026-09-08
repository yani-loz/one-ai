"""
Role: Seals fix-registry row A2 — `project_for_stdout` on an ABORTED block: on a hidden run kind
      it keeps every aborted-shape top-level key (`reason`, `aborted_at_step`, `partial`, the
      identity fields, `cleanup`), reduces hidden-evidence gates to `{status}`, collapses the
      criteria entries of the evaluated hidden split to one per SET and drops `diagnostics`,
      `exclusions` and `opened_outside_closure`; the projection validates as a stdout block
      while the unprojected block validates only as protected — each single leak
      (diagnostics, exclusions, offender paths, a hidden-evidence gate reason, a full hidden
      criteria entry) is refused under the stdout projection; on a tuning run an aborted
      block is unchanged.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.result_block, .exceptions (imported inside each test);
      tests.tools.mem01_verify.result_block_samples (whitelist, hex, aborted tuning block).
Key invariants:
  - The aborted hidden block is built by hand from §3.3/§16.14: an abort at step 9 (observer
    offender) AFTER the gates evaluated, so it carries the very feedback the projection must hide
    (a hidden-evidence gate reason, per-criterion numerators, diagnostics, the offender path).
"""

from __future__ import annotations

import copy
import json

import pytest

from tests.tools.mem01_verify.conftest import GATE_NAMES, InstrumentLoader
from tests.tools.mem01_verify.result_block_samples import (
    HEX,
    STDOUT_WHITELIST,
    VERSION_KEYS,
    aborted_block,
)

ABORT_KEYS = {"reason", "aborted_at_step"}
IDENTITY_KEYS = (
    "release_name",
    "release_state",
    "release_lock_sha256",
    "criteria_sha256",
    "runner_sha256",
    "code_hash",
    "config_hash",
    "corpus_digest",
    "text_digest",
    "migrations_digest",
    "fixtures_digest",
)
OFFENDER = "backend/scripts/foo.py"
HIDDEN_REASON = "content loss on two cases"


def _entry(criterion_id: str, gate: str, *, split: str, status: str, basis: str) -> dict:
    return {
        "id": criterion_id,
        "gate": gate,
        "set": gate,
        "split": split,
        "evidence_basis": basis,
        "acceptance_state": "provisional",
        "kind": "ratio",
        "numerator": 2,
        "denominator": 30,
        "denominator_def": "oracle denominator",
        "operator": "==",
        "threshold": 0,
        "minimum": 30,
        "status": status,
        "reason": "" if status == "PASS" else "oracle reason",
        "expected": 30,
        "evaluated": 30,
        "skipped": 0,
        "errors": 0,
        "diagnostic_only": False,
        "directional": False,
        "versions": {},
    }


def _aborted_hidden_block(run_kind: str, split: str) -> dict:
    """An aborted hidden run (step 9, observer offender) that had already evaluated its gates."""
    gates = {gate: {"status": "skipped", "reason": "aborted"} for gate in GATE_NAMES}
    gates["QS"] = {
        "status": "FAIL",
        "reason": HIDDEN_REASON,
        "criteria": ["qs.no_content_loss", "qs.echo_incidence"],
    }
    gates["NF"] = {"status": "PASS", "criteria": ["nf.noise_stopped"]}
    gates["SNAP"] = {
        "status": "incomplete",
        "reason": "component absent",
        "criteria": ["snap.oracle"],
    }
    basis = f"H-{split}"
    block = {
        "schema": "MEM01_RESULT_V1",
        "phase": "step1",
        "status": "ERROR",
        "aborted": True,
        "reason": "opened outside closure",
        "aborted_at_step": 9,
        "partial": False,
        "run_kind": run_kind,
        "split_evaluated": split,
        **{key: HEX for key in IDENTITY_KEYS[2:]},
        "release_name": "step1-gold-v1",
        "release_state": "frozen",
        "sets": {
            gate: {"expected": 0, "evaluated": 0, "skipped": 0, "errors": 0} for gate in GATE_NAMES
        },
        "gates": gates,
        "criteria": [
            _entry("qs.no_content_loss", "QS", split=split, status="FAIL", basis=basis),
            _entry("qs.echo_incidence", "QS", split=split, status="PASS", basis=basis),
            _entry("nf.noise_stopped", "NF", split=split, status="PASS", basis=basis),
            _entry("snap.oracle", "SNAP", split="fixtures", status="incomplete", basis="F"),
        ],
        "provisional_gates": ["FID", "THR", "IDENT", "ATTR"],
        "directional_gates": [],
        "diagnostics": {**{gate: {} for gate in GATE_NAMES}, "QS": {"cases": 2}},
        "exclusions": [{"id": "x", "reason": "oracle exclusion", "policy_ref": "4.6"}],
        "opened_outside_closure": [OFFENDER],
        "versions": {key: "1.0" for key in VERSION_KEYS},
        "cleanup": {
            "probe_dropped": True,
            "probe_name": "mem01_probe_20260906t120000z_0a1b2c3d",
            "kept": False,
        },
        "run_id": "20260906t120000z_0a1b2c3d",
        "started_at": "2026-09-06T12:00:00+00:00",
        "duration_ms": 9,
    }
    return block  # §3.3/§16.14: an aborted block carries no hidden_budget* and no validation key


HIDDEN_KINDS = [("checkpoint", "test"), ("validation", "validation")]
LEAKS = (
    "diagnostics",
    "exclusions",
    "opened_outside_closure",
    "hidden_gate_reason",
    "hidden_criteria_entries",
)


def _stdout_shape(block: dict) -> dict:
    """The §3.4 stdout shape of an aborted hidden block, written by hand (never the instrument):
    whitelist keys plus the abort keys, hidden-evidence gates reduced to their status, the
    evaluated split's entries collapsed to one per SET (FAIL if any entry failed)."""
    split = block["split_evaluated"]
    # §16.16(q): every H-split gate reduces to {status} on a hidden run kind (roster + evidence)
    hidden_gates = {"QS", "NF", "LANG", "RET"} | {
        entry["gate"] for entry in block["criteria"] if entry["split"] == split
    }
    shaped = {
        key: copy.deepcopy(value)
        for key, value in block.items()
        if key in STDOUT_WHITELIST | ABORT_KEYS
    }
    shaped["gates"] = {
        gate: {"status": entry["status"]} if gate in hidden_gates else copy.deepcopy(entry)
        for gate, entry in block["gates"].items()
    }
    collapsed: dict[str, str] = {}
    kept = []
    for entry in block["criteria"]:
        if entry["split"] != split:
            kept.append(copy.deepcopy(entry))
            continue
        worst = "FAIL" if "FAIL" in (collapsed.get(entry["set"]), entry["status"]) else "PASS"
        collapsed[entry["set"]] = worst
    shaped["criteria"] = kept + [
        {"id": set_name, "split": split, "status": status}
        for set_name, status in sorted(collapsed.items())
    ]
    return shaped


def _with_leak(block: dict, shaped: dict, leak: str) -> dict:
    leaked = copy.deepcopy(shaped)
    if leak in ("diagnostics", "exclusions", "opened_outside_closure"):
        leaked[leak] = copy.deepcopy(block[leak])
    elif leak == "hidden_gate_reason":
        leaked["gates"]["QS"] = copy.deepcopy(block["gates"]["QS"])  # status + reason + ids
    else:
        leaked["criteria"] = copy.deepcopy(block["criteria"])  # the full hidden entries
    return leaked


@pytest.mark.parametrize(("run_kind", "split"), HIDDEN_KINDS)
def test_aborted_hidden_run_projection_keeps_abort_keys_and_hides_the_hidden_feedback(
    instrument: InstrumentLoader, run_kind: str, split: str
) -> None:
    result_block = instrument("result_block")
    block = _aborted_hidden_block(run_kind, split)

    projected = result_block.project_for_stdout(copy.deepcopy(block))

    assert set(projected) <= STDOUT_WHITELIST | ABORT_KEYS
    for key in ("reason", "aborted_at_step", "partial", "run_kind", "cleanup", *IDENTITY_KEYS):
        assert projected[key] == block[key], key
    assert projected["aborted"] is True and projected["status"] == "ERROR"
    assert projected["gates"]["QS"] == {"status": "FAIL"}  # hidden-evidence gate: status only
    assert projected["gates"]["NF"] == {"status": "PASS"}
    assert projected["gates"]["SNAP"]["status"] == "incomplete"
    assert all("status" in gate for gate in projected["gates"].values())
    collapsed = [entry for entry in projected["criteria"] if entry["split"] == split]
    assert sorted(entry["id"] for entry in collapsed) == ["NF", "QS"]
    assert all(set(entry) == {"id", "split", "status"} for entry in collapsed)
    assert {entry["id"]: entry["status"] for entry in collapsed} == {"QS": "FAIL", "NF": "PASS"}
    fixture_entries = [entry for entry in projected["criteria"] if entry["split"] == "fixtures"]
    assert fixture_entries and all("numerator" in entry for entry in fixture_entries)
    for dropped in ("diagnostics", "exclusions", "opened_outside_closure"):
        assert dropped not in projected, dropped
    serialized = json.dumps(projected, ensure_ascii=False)
    for leak in ("qs.no_content_loss", "nf.noise_stopped", HIDDEN_REASON, OFFENDER, '"cases"'):
        assert leak not in serialized, leak


@pytest.mark.parametrize(("run_kind", "split"), HIDDEN_KINDS)
def test_aborted_hidden_projection_validates_as_stdout_while_the_unprojected_block_is_refused(
    instrument: InstrumentLoader, run_kind: str, split: str
) -> None:
    result_block = instrument("result_block")
    exceptions = instrument("exceptions")
    block = _aborted_hidden_block(run_kind, split)

    projected = result_block.project_for_stdout(copy.deepcopy(block))

    result_block.validate_result_block(projected, projection="stdout")
    with pytest.raises(exceptions.ResultBlockError):
        result_block.validate_result_block(block, projection="stdout")  # carries diagnostics


def test_aborted_tuning_blocks_project_to_themselves(instrument: InstrumentLoader) -> None:
    result_block = instrument("result_block")
    early = aborted_block()
    late = _aborted_hidden_block("tuning", "optimization")
    late["release_state"] = "draft"

    assert result_block.project_for_stdout(copy.deepcopy(early)) == early
    assert result_block.project_for_stdout(copy.deepcopy(late)) == late


@pytest.mark.parametrize(("run_kind", "split"), HIDDEN_KINDS)
def test_raw_aborted_hidden_block_validates_as_protected_but_never_as_stdout(
    instrument: InstrumentLoader, run_kind: str, split: str
) -> None:
    result_block = instrument("result_block")
    exceptions = instrument("exceptions")
    block = _aborted_hidden_block(run_kind, split)

    result_block.validate_result_block(block, projection="protected")  # positive control
    with pytest.raises(exceptions.ResultBlockError):
        result_block.validate_result_block(block, projection="stdout")


@pytest.mark.parametrize("leak", LEAKS)
def test_each_single_leak_in_an_aborted_checkpoint_stdout_block_is_refused(
    instrument: InstrumentLoader, leak: str
) -> None:
    result_block = instrument("result_block")
    exceptions = instrument("exceptions")
    block = _aborted_hidden_block("checkpoint", "test")
    shaped = _stdout_shape(block)

    result_block.validate_result_block(shaped, projection="stdout")  # the hand-made shape passes
    with pytest.raises(exceptions.ResultBlockError):
        result_block.validate_result_block(_with_leak(block, shaped, leak), projection="stdout")
