"""
Role: The per-section checkers of the machine block's two list-shaped sections — the `criteria`
      list of §3.3/§3.4 (full entries, their enumerations, and the collapsed form a hidden run's
      stdout projection requires) and the `gates` object (§3.3 statuses and reasons, §3.4/§16.16(q)
      reduction of a hidden-evidence gate to its status alone). Pure predicates over a block.
Used by: `tools.mem01_verify.result_block_schema`, which composes them into the completed and
      aborted shape checks and RE-EXPORTS `is_collapsible` and `CRITERION_FIELDS` (the names
      `result_block.py` and the sealed oracle reach through the schema module).
Depends on: `tools.mem01_verify.statuses` (`CRITERION_STATUSES`, `GATE_NAMES`, `GATE_STATUSES`,
      `H_SPLIT_GATES`, `PASS`, `FAIL`, `PENDING`).
Key invariants:
  - A checker NEVER raises and never stops at the first problem: it appends every violation it
    finds to the caller's list, so one error can name them all (§3.3).
  - The dependency runs ONE way — this module never imports `result_block_schema`, so the pair
    cannot form an import cycle; every constant these checkers need is defined here.
  - `is_collapsible` is the ONE definition of "an entry the stdout projection collapses" — an
    entry on the evaluated hidden split that was scored. A `pending` entry is never collapsed:
    §3.4 keeps `pending` `.validation` entries in their full form because an entry that was
    never scored reveals no hidden case. The projector and the schema import THIS predicate, so
    the block the projector emits is the block the schema accepts.
"""

from __future__ import annotations

from collections.abc import Mapping

from tools.mem01_verify.statuses import (
    CRITERION_STATUSES,
    FAIL,
    GATE_NAMES,
    GATE_STATUSES,
    H_SPLIT_GATES,
    PASS,
    PENDING,
)

COLLAPSED_ENTRY_FIELDS: frozenset[str] = frozenset({"id", "split", "status"})
GATE_ENTRY_FIELDS: frozenset[str] = frozenset({"status", "reason", "criteria"})

CRITERION_FIELDS: tuple[str, ...] = tuple(
    "id gate set split evidence_basis acceptance_state kind numerator denominator "
    "denominator_def operator threshold minimum status reason expected evaluated skipped "
    "errors diagnostic_only directional versions".split()
)
CRITERION_ENUMS: tuple[tuple[str, frozenset[str]], ...] = (
    ("split", frozenset("optimization test validation fixtures corpus".split())),
    ("kind", frozenset({"ratio", "count"})),
    ("status", CRITERION_STATUSES),
    ("operator", frozenset({"==", "<=", ">="})),
    ("evidence_basis", frozenset("F C F+C H-optimization H-test H-validation".split())),
    ("acceptance_state", frozenset({"provisional", "validated"})),
)


def is_collapsible(entry: object, split_evaluated: str) -> bool:
    """True when the stdout projection of a hidden run collapses this criteria entry (§3.4).

    Args:
        entry: A criteria entry (anything else is not collapsible).
        split_evaluated: The block's evaluated hidden split.

    Returns:
        True for a scored entry on the evaluated hidden split. Entries on the
        `fixtures`/`corpus`/`optimization` splits keep their full form because they carry no
        hidden case, and so does a `pending` entry on the evaluated split — it was never
        scored, so it discloses nothing to collapse.
    """
    if not isinstance(entry, Mapping):
        return False
    return entry.get("split") == split_evaluated and entry.get("status") != PENDING


def _check_criterion_entry(index: int, entry: Mapping[str, object], problems: list[str]) -> None:
    """Check one FULL criteria entry against the §3.4 field list and enumerations."""
    missing = [field for field in CRITERION_FIELDS if field not in entry]
    if missing:
        problems.append(f"criteria entry {index} is missing {sorted(missing)}")
    for field, allowed in CRITERION_ENUMS:
        if field in entry and entry[field] not in allowed:
            problems.append(f"criteria entry {index} has an invalid {field}: {entry[field]!r}")


def check_criteria(
    block: Mapping[str, object], *, strict: bool, split_evaluated: str, problems: list[str]
) -> frozenset[str]:
    """Check the criteria list.

    Args:
        block: The block under validation.
        strict: True for a HIDDEN run's stdout projection, where entries on the evaluated
            hidden split must already be collapsed to `{id, split, status}`.
        split_evaluated: The block's `split_evaluated` (ignored unless `strict`).
        problems: Accumulator the violations are appended to.

    Returns:
        The SET names whose entries were collapsed — the hidden-evidence gates, whose gate
        entries must project to their status alone. A `pending` entry on the evaluated split
        is not collapsed (§3.4) and does not name its SET a hidden-evidence gate.
    """
    entries = block.get("criteria")
    if not isinstance(entries, list):
        problems.append("criteria must be a list")
        return frozenset()
    collapsed: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            problems.append(f"criteria entry {index} must be an object")
            continue
        if strict and is_collapsible(entry, split_evaluated):
            collapsed.add(str(entry.get("id")))
            if set(entry) != COLLAPSED_ENTRY_FIELDS or entry.get("status") not in (PASS, FAIL):
                problems.append(
                    f"criteria entry {index} on the evaluated hidden split must collapse to "
                    f"{sorted(COLLAPSED_ENTRY_FIELDS)} with a PASS/FAIL status"
                )
            continue
        _check_criterion_entry(index, entry, problems)
    return frozenset(collapsed)


def check_gates(
    block: Mapping[str, object],
    *,
    strict: bool,
    hidden_gates: frozenset[str],
    require_criteria: bool,
    problems: list[str],
) -> None:
    """Check the per-gate entries of §3.3 and, when `strict`, their §3.4 projection.

    `hidden_gates` are the SETs whose criteria entries collapsed, joined when `strict` by the
    roster `H_SPLIT_GATES` (§16.16(q)); `require_criteria` asks for each gate's criterion ids.
    """
    gates = block.get("gates")
    if not isinstance(gates, Mapping):
        problems.append("gates must be an object")
        return
    reduced = hidden_gates | frozenset(H_SPLIT_GATES) if strict else hidden_gates
    if set(gates) != set(GATE_NAMES):
        problems.append(f"gates must name exactly the 17 gates, got {sorted(gates)}")
    for name, entry in gates.items():
        if not isinstance(entry, Mapping):
            problems.append(f"gate {name} must be an object")
            continue
        if entry.get("status") not in GATE_STATUSES:
            problems.append(f"gate {name} has an invalid status: {entry.get('status')!r}")
        if not strict:
            if entry.get("status") != PASS and not entry.get("reason"):
                problems.append(f"gate {name} is not PASS and states no reason")
            if require_criteria and "criteria" not in entry:
                problems.append(f"gate {name} is missing its criteria ids")
            continue
        if not set(entry) <= GATE_ENTRY_FIELDS:
            problems.append(f"gate {name} carries fields outside {sorted(GATE_ENTRY_FIELDS)}")
        if name in reduced and set(entry) != {"status"}:
            problems.append(f"hidden-evidence gate {name} must project to its status alone")
