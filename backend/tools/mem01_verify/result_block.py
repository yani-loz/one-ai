"""
Role: The machine block of contract §3.3/§3.4 as a wire format — rendering it between the two
      exact marker lines, recovering exactly one block from a stdout capture, computing the
      hidden-safe stdout projection, and raising the single schema error over the violations
      `result_block_schema` collects.
Used by: the runner `verify_step1` (render / project / validate before printing and before
      writing `protected_result.json`) and the sealed oracle; sealed by
      `tests/tools/mem01_verify/test_result_block.py` and `test_result_block_schema.py`.
Depends on: `tools.mem01_verify.exceptions`, `.statuses` and `.result_block_schema`.
Key invariants:
  - Rendering is `json.dumps(..., ensure_ascii=False, sort_keys=True, indent=1)` wrapped in
    `MEM01_RESULT_V1_BEGIN` / `MEM01_RESULT_V1_END`; parsing refuses zero or two blocks.
  - The stdout projection of a HIDDEN run keeps only the §3.4 whitelist, reduces every
    hidden-evidence gate to its status (dropping its reason and its criterion ids), and
    collapses every SCORED criteria entry on the evaluated hidden split to ONE
    `{id, split, status}` entry per SET. On tuning runs the projection is the protected
    result unchanged.
  - A `pending` entry on the evaluated split keeps its FULL form (§3.4: `pending`
    `.validation` entries reveal no hidden case — they were never scored) and does not make
    its SET a hidden-evidence gate.
  - The hidden-evidence gates are ROSTER-based (§16.16(q)): every `H_SPLIT_GATES` gate is
    reduced on a hidden run kind whether or not it emitted an entry on the evaluated split,
    joined by any other SET that did emit one — so an H gate that stayed `incomplete` cannot
    keep its reason on stdout.
  - An ABORTED block projects the same way (§16.16d): the whitelist widens by the aborted
    shape's `reason` and `aborted_at_step`, and `diagnostics`, `exclusions` and
    `opened_outside_closure` are dropped like they are on a completed hidden run — so the
    runner may print `project_for_stdout(block)` for EVERY run, with no special case.
  - `validate_result_block` never raises on the first problem: one error names them all.
  - Rendering does not validate; the caller validates the projection it is about to print.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Literal

from tools.mem01_verify.exceptions import ResultBlockError
from tools.mem01_verify.result_block_schema import (
    ABORTED_STDOUT_FIELDS,
    HIDDEN_RUN_KINDS,
    PHASE_NAME,
    SCHEMA_NAME,
    STDOUT_FIELDS,
    collect_violations,
    is_collapsible,
)
from tools.mem01_verify.statuses import FAIL, H_SPLIT_GATES, PASS

RESULT_BLOCK_BEGIN = "MEM01_RESULT_V1_BEGIN"
RESULT_BLOCK_END = "MEM01_RESULT_V1_END"

__all__ = [
    "PHASE_NAME",
    "RESULT_BLOCK_BEGIN",
    "RESULT_BLOCK_END",
    "SCHEMA_NAME",
    "parse_result_block",
    "project_for_stdout",
    "render_result_block",
    "validate_result_block",
]


def render_result_block(block: Mapping[str, object]) -> str:
    """Render the machine block between the two marker lines (§3.3).

    Args:
        block: The protected result or its stdout projection.

    Returns:
        `MEM01_RESULT_V1_BEGIN`, the JSON object (`ensure_ascii=False`, sorted keys, indent 1)
        and `MEM01_RESULT_V1_END`, newline-separated, without a trailing newline.

    Raises:
        ResultBlockError: The block is not JSON-serializable.
    """
    try:
        body = json.dumps(block, ensure_ascii=False, sort_keys=True, indent=1)
    except (TypeError, ValueError) as exc:
        raise ResultBlockError(f"the machine block is not JSON-serializable: {exc}") from exc
    return f"{RESULT_BLOCK_BEGIN}\n{body}\n{RESULT_BLOCK_END}"


def parse_result_block(stdout: str) -> dict:
    """Recover the single machine block embedded in a stdout capture.

    Args:
        stdout: Everything the run printed; lines around the block are ignored.

    Returns:
        The parsed JSON object.

    Raises:
        ResultBlockError: Zero or more than one block, markers out of order, or a body that is
            not a JSON object.
    """
    lines = stdout.splitlines()
    begins = [index for index, line in enumerate(lines) if line.strip() == RESULT_BLOCK_BEGIN]
    ends = [index for index, line in enumerate(lines) if line.strip() == RESULT_BLOCK_END]
    if len(begins) != 1 or len(ends) != 1:
        raise ResultBlockError(
            f"expected exactly one machine block, found {len(begins)} begin and {len(ends)} "
            "end markers"
        )
    if ends[0] < begins[0]:
        raise ResultBlockError("the machine block's end marker precedes its begin marker")
    try:
        parsed = json.loads("\n".join(lines[begins[0] + 1 : ends[0]]))
    except json.JSONDecodeError as exc:
        raise ResultBlockError(f"the machine block is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ResultBlockError("the machine block must be a JSON object")
    return parsed


def _collapse_status(statuses: list[str]) -> str:
    """A SET's collapsed hidden verdict: PASS only when every one of its entries passed."""
    return PASS if statuses and all(status == PASS for status in statuses) else FAIL


def _project_criteria(entries: list, split_evaluated: str) -> tuple[list, frozenset[str]]:
    """Collapse the SCORED entries on the evaluated hidden split to one per SET (§3.4).

    Returns:
        The projected criteria list (collapsed entries take the position of the first collapsed
        entry of their SET, every other entry — including a `pending` one on the evaluated
        split — is copied through) and the collapsed SET names.
    """
    per_set: dict[str, list[str]] = {}
    for entry in entries:
        if is_collapsible(entry, split_evaluated):
            per_set.setdefault(str(entry.get("set")), []).append(str(entry.get("status")))
    projected: list = []
    emitted: set[str] = set()
    for entry in entries:
        if not is_collapsible(entry, split_evaluated):
            projected.append(deepcopy(entry))
            continue
        set_name = str(entry.get("set"))
        if set_name in emitted:
            continue
        emitted.add(set_name)
        status = _collapse_status(per_set[set_name])
        projected.append({"id": set_name, "split": split_evaluated, "status": status})
    return projected, frozenset(emitted)


def _project_gates(gates: Mapping[str, object], hidden_gates: frozenset[str]) -> dict:
    """Reduce hidden-evidence gates to their status; copy the other gates through (§3.4)."""
    projected: dict = {}
    for name, entry in gates.items():
        if name in hidden_gates and isinstance(entry, Mapping):
            projected[name] = {"status": entry.get("status")}
        else:
            projected[name] = deepcopy(entry)
    return projected


def project_for_stdout(block: Mapping[str, object]) -> dict:
    """Return the hidden-safe projection of a machine block (§3.4).

    Args:
        block: The protected result.

    Returns:
        On `tuning` runs a deep copy of the block — the projection equals the protected result,
        aborted or completed alike.
        On `checkpoint` / `validation` runs a NEW block carrying only the §3.4 whitelist, with
        the hidden split's criteria collapsed to one entry per SET and EVERY `H_SPLIT_GATES`
        gate reduced to its status — plus any other SET that emitted an entry on the evaluated
        split — so no hidden case, reason or criterion id reaches stdout (R4, R5, §16.16(q)).
        An ABORTED block keeps its `reason` and `aborted_at_step` beside that whitelist
        (§16.16d); `diagnostics`, `exclusions` and `opened_outside_closure` are dropped either
        way.
    """
    if block.get("run_kind") not in HIDDEN_RUN_KINDS:
        return deepcopy(dict(block))
    allowed = STDOUT_FIELDS
    if block.get("aborted") is True:
        allowed = STDOUT_FIELDS | ABORTED_STDOUT_FIELDS
    projected = {key: deepcopy(value) for key, value in block.items() if key in allowed}
    entries = block.get("criteria")
    hidden_gates = frozenset(H_SPLIT_GATES)
    if isinstance(entries, list):
        projected["criteria"], emitted = _project_criteria(
            entries, str(block.get("split_evaluated"))
        )
        hidden_gates |= emitted
    gates = block.get("gates")
    if isinstance(gates, Mapping):
        projected["gates"] = _project_gates(gates, hidden_gates)
    return projected


def validate_result_block(
    block: Mapping[str, object], *, projection: Literal["protected", "stdout"]
) -> None:
    """Validate a machine block against the §3.3/§3.4 schema of the given projection.

    Args:
        block: A completed-run block or an aborted-run block.
        projection: `"protected"` for the artifact written to the report dir, `"stdout"` for
            what is printed. The two differ only on HIDDEN runs; on tuning runs the stdout
            projection equals the protected result, so both accept the same shape.

    Raises:
        ResultBlockError: One error naming EVERY violation found, so a caller sees the whole
            schema failure at once rather than the first field that broke.
    """
    if projection not in ("protected", "stdout"):
        raise ResultBlockError(f"unknown projection {projection!r}")
    if not isinstance(block, Mapping):
        raise ResultBlockError("the machine block must be a JSON object")
    problems = collect_violations(block, projection)
    if problems:
        raise ResultBlockError(
            "machine block violates §3.3/§3.4: " + "; ".join(sorted(set(problems)))
        )
