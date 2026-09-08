"""
Role: Seals the machine block of contract §3.3/§3.4 — rendering (BEGIN/END exactly once, sorted
      keys, indent 1, ensure_ascii=False), parsing, schema validation that lists every
      violation, and the hidden-safe stdout projection.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.result_block and .exceptions (imported inside each test).
Key invariants:
  - The valid blocks below are built by hand from the §3.3 field table; a test that mutates one
    field pairs the rejection with the untouched block passing.
"""

from __future__ import annotations

import copy
import json

import pytest

from tests.tools.mem01_verify.conftest import InstrumentLoader
from tests.tools.mem01_verify.result_block_samples import STDOUT_WHITELIST, completed_block


def test_render_wraps_one_sorted_indent1_non_ascii_json_between_exact_lines(
    instrument: InstrumentLoader,
) -> None:
    result_block = instrument("result_block")
    block = completed_block()
    block["reason"] = "причина"

    rendered = result_block.render_result_block(block)

    lines = rendered.strip().splitlines()
    assert lines[0] == "MEM01_RESULT_V1_BEGIN" and lines[-1] == "MEM01_RESULT_V1_END"
    body = "\n".join(lines[1:-1])
    assert "причина" in body and "\\u0" not in body
    assert lines[1] == "{" and lines[2].startswith(' "aborted"')
    pairs = json.loads(body, object_pairs_hook=lambda kv: kv)
    assert [key for key, _ in pairs] == sorted(key for key, _ in pairs)
    assert result_block.RESULT_BLOCK_BEGIN == "MEM01_RESULT_V1_BEGIN"
    assert result_block.RESULT_BLOCK_END == "MEM01_RESULT_V1_END"


def test_parse_recovers_the_rendered_block_and_ignores_surrounding_lines(
    instrument: InstrumentLoader,
) -> None:
    result_block = instrument("result_block")
    block = completed_block()
    stdout = (
        "MEM01 UTF-8 self-test: Здравей\n" + result_block.render_result_block(block) + "\nSTEP1\n"
    )

    parsed = result_block.parse_result_block(stdout)

    assert parsed == block


def test_parse_refuses_zero_and_two_blocks(instrument: InstrumentLoader) -> None:
    result_block = instrument("result_block")
    exceptions = instrument("exceptions")
    rendered = result_block.render_result_block(completed_block())

    with pytest.raises(exceptions.ResultBlockError):
        result_block.parse_result_block("no block\n")
    with pytest.raises(exceptions.ResultBlockError):
        result_block.parse_result_block(rendered + "\n" + rendered)
    assert result_block.parse_result_block(rendered)["schema"] == "MEM01_RESULT_V1"


def test_project_for_stdout_is_identity_on_tuning_runs(instrument: InstrumentLoader) -> None:
    result_block = instrument("result_block")
    block = completed_block("tuning")

    projected = result_block.project_for_stdout(copy.deepcopy(block))

    assert projected == block


def test_project_for_stdout_collapses_hidden_entries_and_drops_detail_on_checkpoint(
    instrument: InstrumentLoader,
) -> None:
    result_block = instrument("result_block")
    block = completed_block("checkpoint")

    projected = result_block.project_for_stdout(copy.deepcopy(block))

    assert set(projected) <= STDOUT_WHITELIST
    assert "diagnostics" not in projected and "exclusions" not in projected
    assert "opened_outside_closure" not in projected
    collapsed = [entry for entry in projected["criteria"] if entry["split"] == "test"]
    assert sorted(entry["id"] for entry in collapsed) == ["NF", "QS"]
    assert all(set(entry) == {"id", "split", "status"} for entry in collapsed)
    assert {entry["id"]: entry["status"] for entry in collapsed} == {"QS": "FAIL", "NF": "PASS"}
    assert not any("qs.no_content_loss" == entry.get("id") for entry in projected["criteria"])
    fixture_entries = [entry for entry in projected["criteria"] if entry["split"] == "fixtures"]
    assert fixture_entries and all("numerator" in entry for entry in fixture_entries)
    assert projected["gates"]["QS"] == {"status": "FAIL"}  # hidden-evidence gate: status only
    assert projected["gates"]["NF"] == {"status": "PASS"}
    assert all("status" in gate for gate in projected["gates"].values())
    serialized = json.dumps(projected, ensure_ascii=False)
    assert "qs.no_content_loss" not in serialized and "nf.noise_stopped" not in serialized
    assert "content loss" not in serialized  # the hidden-evidence gate reason
    assert projected["hidden_budget"] == "1/20"
    assert projected["gates"]["SNAP"]["status"] == "PASS"


def test_projected_checkpoint_block_validates_as_stdout_but_not_as_protected(
    instrument: InstrumentLoader,
) -> None:
    result_block = instrument("result_block")
    exceptions = instrument("exceptions")
    projected = result_block.project_for_stdout(completed_block("checkpoint"))

    result_block.validate_result_block(projected, projection="stdout")
    with pytest.raises(exceptions.ResultBlockError):
        result_block.validate_result_block(projected, projection="protected")


def test_result_block_error_is_a_mem01_error(instrument: InstrumentLoader) -> None:
    exceptions = instrument("exceptions")

    assert issubclass(exceptions.ResultBlockError, exceptions.Mem01Error)
