"""
Role: Seals the status algebra of contract §3.5 — gate status precedence over criteria,
      diagnostic-only entries never counting, block status precedence (integrity, aborted,
      partial, ERROR/incomplete/skipped, FAIL, PASS) and the exit codes.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.statuses (imported inside each test).
Key invariants:
  - Every precedence case pairs with the case one rung below it, so a swapped rung goes red.
"""

from __future__ import annotations

import pytest

from tests.tools.mem01_verify.conftest import InstrumentLoader


def _criteria(*statuses: str) -> list[dict]:
    return [
        {"status": status, "diagnostic_only": False, "directional": False} for status in statuses
    ]


def test_status_constants_have_the_contract_values(instrument: InstrumentLoader) -> None:
    statuses = instrument("statuses")

    assert (statuses.PASS, statuses.FAIL, statuses.ERROR) == ("PASS", "FAIL", "ERROR")
    assert (statuses.INCOMPLETE, statuses.PENDING, statuses.SKIPPED) == (
        "incomplete",
        "pending",
        "skipped",
    )
    assert (statuses.EXIT_PASS, statuses.EXIT_FAIL, statuses.EXIT_ERROR) == (0, 1, 2)


@pytest.mark.parametrize(
    ("criteria", "expected"),
    [
        (("PASS", "FAIL", "ERROR", "incomplete"), "ERROR"),
        (("PASS", "incomplete", "FAIL"), "incomplete"),
        (("PASS", "FAIL"), "FAIL"),
        (("PASS", "PASS"), "PASS"),
        (("PASS", "pending"), "PASS"),
        (("FAIL", "pending"), "FAIL"),
        (("pending", "incomplete"), "incomplete"),
    ],
)
def test_derive_gate_status_follows_error_incomplete_fail_pass_precedence(
    instrument: InstrumentLoader, criteria: tuple[str, ...], expected: str
) -> None:
    statuses = instrument("statuses")

    assert statuses.derive_gate_status(_criteria(*criteria)) == expected


def test_derive_gate_status_ignores_diagnostic_only_entries(instrument: InstrumentLoader) -> None:
    statuses = instrument("statuses")
    diagnostic_fail = {"status": "FAIL", "diagnostic_only": True, "directional": False}
    diagnostic_na = {"status": "N/A", "diagnostic_only": True, "directional": False}

    assert statuses.derive_gate_status([*_criteria("PASS"), diagnostic_fail]) == "PASS"
    assert statuses.derive_gate_status([diagnostic_na, *_criteria("PASS")]) == "PASS"
    # positive control: the same FAIL as a mandatory entry decides the gate
    assert statuses.derive_gate_status(_criteria("PASS", "FAIL")) == "FAIL"


def _gates(**named: str) -> dict[str, dict]:
    return {name: {"status": status} for name, status in named.items()}


def test_derive_block_status_aborted_or_integrity_failure_is_error_even_when_all_pass(
    instrument: InstrumentLoader,
) -> None:
    statuses = instrument("statuses")
    all_pass = _gates(SNAP="PASS", VIS="PASS")

    assert (
        statuses.derive_block_status(gates=all_pass, partial=False, aborted=True, integrity_ok=True)
        == "ERROR"
    )
    assert (
        statuses.derive_block_status(
            gates=all_pass, partial=False, aborted=False, integrity_ok=False
        )
        == "ERROR"
    )
    # positive control
    assert (
        statuses.derive_block_status(
            gates=all_pass, partial=False, aborted=False, integrity_ok=True
        )
        == "PASS"
    )


def test_derive_block_status_partial_run_is_error(instrument: InstrumentLoader) -> None:
    statuses = instrument("statuses")

    assert (
        statuses.derive_block_status(
            gates=_gates(SNAP="PASS"), partial=True, aborted=False, integrity_ok=True
        )
        == "ERROR"
    )


@pytest.mark.parametrize(
    ("gates", "expected"),
    [
        (_gates(SNAP="PASS", QS="ERROR", TIME="FAIL"), "ERROR"),
        (_gates(SNAP="PASS", QS="incomplete", TIME="FAIL"), "ERROR"),
        (_gates(SNAP="PASS", QS="skipped", TIME="FAIL"), "ERROR"),
        (_gates(SNAP="PASS", TIME="FAIL"), "FAIL"),
        (_gates(SNAP="PASS", TIME="PASS"), "PASS"),
    ],
)
def test_derive_block_status_precedence_over_gate_statuses(
    instrument: InstrumentLoader, gates: dict[str, dict], expected: str
) -> None:
    statuses = instrument("statuses")

    assert (
        statuses.derive_block_status(gates=gates, partial=False, aborted=False, integrity_ok=True)
        == expected
    )


@pytest.mark.parametrize(("block_status", "code"), [("PASS", 0), ("FAIL", 1), ("ERROR", 2)])
def test_exit_code_for_maps_block_status(
    instrument: InstrumentLoader, block_status: str, code: int
) -> None:
    statuses = instrument("statuses")

    assert statuses.exit_code_for(block_status) == code


def test_exit_code_for_refuses_a_value_outside_the_three_block_statuses(
    instrument: InstrumentLoader,
) -> None:
    statuses = instrument("statuses")
    exceptions = instrument("exceptions")

    with pytest.raises(exceptions.ResultBlockError):
        statuses.exit_code_for("bogus")
    with pytest.raises(exceptions.ResultBlockError):
        statuses.exit_code_for("incomplete")
    assert statuses.exit_code_for("ERROR") == 2  # positive control


@pytest.mark.parametrize(
    "entries",
    [
        [],
        _criteria("pending"),
        _criteria("pending", "pending"),
        [{"status": "FAIL", "diagnostic_only": True, "directional": False}],
        [{"status": "PASS", "diagnostic_only": False, "directional": True}],
        [
            {"status": "N/A", "diagnostic_only": True, "directional": False},
            {"status": "PASS", "diagnostic_only": False, "directional": True},
            *_criteria("pending"),
        ],
    ],
    ids=[
        "empty",
        "one_pending",
        "all_pending",
        "diagnostic_only",
        "directional_only",
        "mixed_non_deciding",
    ],
)
def test_derive_gate_status_is_incomplete_when_no_entry_can_decide(
    instrument: InstrumentLoader, entries: list[dict]
) -> None:
    statuses = instrument("statuses")

    assert statuses.derive_gate_status(entries) == "incomplete"
    # positive control: one mandatory PASS beside the same entries decides the gate
    assert statuses.derive_gate_status([*entries, *_criteria("PASS")]) == "PASS"
