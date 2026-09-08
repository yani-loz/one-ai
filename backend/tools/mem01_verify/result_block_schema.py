"""
Role: The schema of the machine block — the §3.3/§3.4 field tables (which fields each shape
      requires, the stdout whitelist, the enumerations) and the shape checkers that turn a block
      into the LIST of everything wrong with it. Pure data plus pure predicates; it never
      renders, parses, prints or raises. The two list-shaped sections — `criteria` and `gates` —
      are checked by the sibling `result_block_checks`, whose `is_collapsible` and
      `CRITERION_FIELDS` this module RE-EXPORTS.
Used by: `tools.mem01_verify.result_block` (`validate_result_block` raises the one error over
      `collect_violations`, `project_for_stdout` reads `STDOUT_FIELDS` and `is_collapsible`);
      sealed indirectly by `tests/tools/mem01_verify/test_result_block_schema.py`.
Depends on: `tools.mem01_verify.result_block_checks` (the criteria and gate checkers) and
      `.statuses` (the block-status vocabulary and the gate roster).
Key invariants:
  - A checker NEVER raises and never stops at the first problem: it appends every violation it
    finds to the caller's list, so one error can name them all (§3.3).
  - The stdout schema differs from the protected schema only on HIDDEN runs; on tuning runs the
    stdout projection equals the protected result and both accept the same shape (§3.4).
  - `hidden_budget*` and `validation` are present exactly on their run kind, and ABSENT (never
    null) on an aborted run (§16.14).
  - The aborted shape is permissive about EXTRA fields: a run aborted after step 3 legitimately
    carries release identity that a run aborted at step 3 does not — EXCEPT under the stdout
    projection of a hidden run, where §16.16(d) applies the §3.4 whitelist to the aborted
    shape too (`ABORTED_STDOUT_FIELDS` are the only additions) and rejects `diagnostics`,
    `exclusions`, `opened_outside_closure`, a hidden-evidence gate's reason and an
    uncollapsed hidden criteria entry.
  - §16.16(p) it REQUIRES `split_evaluated` on an aborted HIDDEN block (a tuning one is
    unaffected); §16.16(q) its hidden-evidence gates are the roster `H_SPLIT_GATES` joined with
    the collapsed SETs, so an H gate with no entry on the evaluated split still reduces.
  - The ONE definition of "an entry the stdout projection collapses" is
    `result_block_checks.is_collapsible`; this module re-exports it rather than restating it, so
    the block the projector emits is the block this schema accepts.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

# The per-section checkers live in `result_block_checks`; `is_collapsible` and
# `CRITERION_FIELDS` are RE-EXPORTED here because `result_block` reaches them through the
# schema module. The dependency runs one way only, so the pair cannot cycle.
from tools.mem01_verify.result_block_checks import (
    CRITERION_FIELDS as CRITERION_FIELDS,
)
from tools.mem01_verify.result_block_checks import (
    check_criteria,
    check_gates,
)
from tools.mem01_verify.result_block_checks import (
    is_collapsible as is_collapsible,
)
from tools.mem01_verify.statuses import (
    BLOCK_STATUSES,
    ERROR,
    GATE_NAMES,
    HOLDOUT_GATES,
)

SCHEMA_NAME = "MEM01_RESULT_V1"
PHASE_NAME = "step1"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
RUN_KINDS: tuple[str, ...] = ("tuning", "checkpoint", "validation")
HIDDEN_RUN_KINDS: tuple[str, ...] = ("checkpoint", "validation")
SPLIT_FOR_RUN_KIND: dict[str, str] = {
    "tuning": "optimization",
    "checkpoint": "test",
    "validation": "validation",
}

HASH_FIELDS: tuple[str, ...] = tuple(
    "release_lock_sha256 criteria_sha256 runner_sha256 code_hash config_hash corpus_digest "
    "text_digest migrations_digest fixtures_digest".split()
)
HIDDEN_BUDGET_FIELDS: tuple[str, ...] = tuple(
    "hidden_budget hidden_budget_by_split hidden_budget_limit hidden_invocations_under_lock".split()
)
VERSION_KEYS: frozenset[str] = frozenset(
    "python sqlalchemy asyncpg postgres pgvector charset_normalizer html2text striprtf "
    "pdfplumber pypdf python_docx openpyxl tnefparse".split()
)
CORPUS_FIELDS: frozenset[str] = frozenset(
    "org_id host port database emails attachments snapshot_transaction_id".split()
)
CLEANUP_FIELDS: frozenset[str] = frozenset({"probe_dropped", "probe_name", "kept"})
SET_COUNTERS: tuple[str, ...] = ("expected", "evaluated", "skipped", "errors")
#: Fields every block shape carries.
ENVELOPE_FIELDS: tuple[str, ...] = tuple(
    "schema phase status aborted run_kind run_id started_at duration_ms".split()
)
#: Fields a completed run carries in BOTH projections.
COMPLETED_FIELDS: tuple[str, ...] = (
    *"partial split_evaluated release_name release_state".split(),
    *HASH_FIELDS,
    *"sets gates criteria provisional_gates directional_gates".split(),
    *"repeats_required repeats_completed cache_policy cache_hits versions cleanup".split(),
)
#: Fields the protected result carries and a HIDDEN run's stdout projection must not.
PROTECTED_ONLY_FIELDS: tuple[str, ...] = tuple(
    "corpus diagnostics exclusions opened_outside_closure".split()
)
#: Fields an aborted run carries beyond the envelope.
ABORTED_FIELDS: tuple[str, ...] = ("reason", "aborted_at_step", "gates", "criteria", *HASH_FIELDS)
#: The aborted-shape keys a HIDDEN run's stdout projection keeps beyond the §3.4 whitelist.
ABORTED_STDOUT_FIELDS: frozenset[str] = frozenset({"reason", "aborted_at_step"})
#: The §3.4 whitelist — the only fields a HIDDEN run's stdout projection may carry.
STDOUT_FIELDS: frozenset[str] = frozenset(
    {*ENVELOPE_FIELDS, *COMPLETED_FIELDS, *HIDDEN_BUDGET_FIELDS, "validation"}
)


def _is_int(value: object) -> bool:
    """True for a real integer — `bool` is an `int` in Python and is not one here."""
    return isinstance(value, int) and not isinstance(value, bool)


def _require_hex(field: str, value: object, *, nullable: bool, problems: list[str]) -> None:
    """Require a 64-character lowercase hex digest (or null where the shape allows it)."""
    if value is None and nullable:
        return
    if not isinstance(value, str) or not HEX64.match(value):
        problems.append(f"{field} must be 64 lowercase hex characters")


def _require_keys(
    block: Mapping[str, object], field: str, expected: frozenset[str], problems: list[str]
) -> Mapping[str, object] | None:
    """Require `field` to be an object whose keys are exactly `expected`; return it."""
    value = block.get(field)
    if not isinstance(value, Mapping):
        problems.append(f"{field} must be an object")
        return None
    if set(value) != expected:
        problems.append(f"{field} must carry exactly the keys {sorted(expected)}")
    return value


def check_envelope(block: Mapping[str, object], problems: list[str]) -> None:
    """Check the fields every block shape carries (§3.3)."""
    for field in ENVELOPE_FIELDS:
        if field not in block:
            problems.append(f"missing {field}")
    if "schema" in block and block["schema"] != SCHEMA_NAME:
        problems.append(f"schema must be {SCHEMA_NAME!r}")
    if "phase" in block and block["phase"] != PHASE_NAME:
        problems.append(f"phase must be {PHASE_NAME!r}")
    if "status" in block and block["status"] not in BLOCK_STATUSES:
        problems.append(f"status must be one of {BLOCK_STATUSES}")
    if "aborted" in block and not isinstance(block["aborted"], bool):
        problems.append("aborted must be a boolean")
    if "run_kind" in block and block["run_kind"] not in RUN_KINDS:
        problems.append(f"run_kind must be one of {RUN_KINDS}")
    if "run_id" in block and not (isinstance(block["run_id"], str) and block["run_id"]):
        problems.append("run_id must be a non-empty string")
    if "started_at" in block and not isinstance(block["started_at"], str):
        problems.append("started_at must be a string")
    if "duration_ms" in block and not _is_int(block["duration_ms"]):
        problems.append("duration_ms must be an integer")


def _check_sets(block: Mapping[str, object], problems: list[str]) -> None:
    """Check the per-SET counters of §3.3."""
    sets = _require_keys(block, "sets", frozenset(GATE_NAMES), problems)
    if sets is None:
        return
    for name, counters in sets.items():
        if not isinstance(counters, Mapping) or not all(
            _is_int(counters.get(counter)) for counter in SET_COUNTERS
        ):
            problems.append(f"set {name} must carry integer {list(SET_COUNTERS)}")


def _check_gate_lists(block: Mapping[str, object], problems: list[str]) -> None:
    """Check `provisional_gates` (frozen order) and `directional_gates` (known gate names)."""
    provisional = block.get("provisional_gates")
    if not isinstance(provisional, list):
        problems.append("provisional_gates must be a list")
    elif provisional != [name for name in HOLDOUT_GATES if name in provisional]:
        problems.append(f"provisional_gates must be {HOLDOUT_GATES} filtered, in that order")
    directional = block.get("directional_gates")
    if not isinstance(directional, list) or any(name not in GATE_NAMES for name in directional):
        problems.append("directional_gates must be a list of gate names")


def _check_conditional_fields(
    block: Mapping[str, object], run_kind: object, problems: list[str]
) -> None:
    """`hidden_budget*` exists exactly on checkpoint runs, `validation` on validation runs."""
    for field in HIDDEN_BUDGET_FIELDS:
        if (field in block) != (run_kind == "checkpoint"):
            problems.append(f"{field} is present on checkpoint runs and absent otherwise")
    if ("validation" in block) != (run_kind == "validation"):
        problems.append("validation is present on validation runs and absent otherwise")
    elif "validation" in block and block["validation"] != "complete":
        problems.append("validation must be 'complete'")


def _check_completed_scalars(block: Mapping[str, object], problems: list[str]) -> None:
    """Check the flat completed-run fields whose values §3.3 constrains."""
    if "partial" in block and not isinstance(block["partial"], bool):
        problems.append("partial must be a boolean")
    if block.get("release_state") not in ("draft", "frozen"):
        problems.append("release_state must be 'draft' or 'frozen'")
    if not (isinstance(block.get("release_name"), str) and block.get("release_name")):
        problems.append("release_name must be a non-empty string")
    for field in ("repeats_required", "repeats_completed"):
        if not _is_int(block.get(field)):
            problems.append(f"{field} must be an integer")
    if block.get("cache_policy") != "forbidden":
        problems.append("cache_policy must be 'forbidden'")
    if block.get("cache_hits") != 0:
        problems.append("cache_hits must be 0")
    for field in HASH_FIELDS:
        if field in block:
            _require_hex(field, block[field], nullable=False, problems=problems)
    _require_keys(block, "versions", VERSION_KEYS, problems)
    cleanup = _require_keys(block, "cleanup", CLEANUP_FIELDS, problems)
    if cleanup is not None and not (
        isinstance(cleanup.get("probe_dropped"), bool)
        and isinstance(cleanup.get("kept"), bool)
        and (cleanup.get("probe_name") is None or isinstance(cleanup.get("probe_name"), str))
    ):
        problems.append("cleanup carries probe_dropped/kept booleans and a probe_name or null")


def _check_protected_only(block: Mapping[str, object], problems: list[str]) -> None:
    """Check the fields only the protected result (and a tuning projection) carries."""
    if (block.get("status") == ERROR) != ("reason" in block):
        problems.append("reason is required when status is ERROR and absent otherwise")
    if not isinstance(block.get("diagnostics"), Mapping):
        problems.append("diagnostics must be an object")
    for field in ("exclusions", "opened_outside_closure"):
        if not isinstance(block.get(field), list):
            problems.append(f"{field} must be a list")
    _require_keys(block, "corpus", CORPUS_FIELDS, problems)


def check_completed(block: Mapping[str, object], projection: str, problems: list[str]) -> None:
    """Check a completed-run block against the protected or the stdout schema (§3.3/§3.4)."""
    run_kind = block.get("run_kind")
    strict = projection == "stdout" and run_kind in HIDDEN_RUN_KINDS
    required = COMPLETED_FIELDS if strict else COMPLETED_FIELDS + PROTECTED_ONLY_FIELDS
    for field in required:
        if field not in block:
            problems.append(f"missing {field}")
    if strict:
        outside = set(block) - STDOUT_FIELDS
        if outside:
            problems.append(f"fields outside the §3.4 stdout whitelist: {sorted(outside)}")
    else:
        _check_protected_only(block, problems)
    expected_split = SPLIT_FOR_RUN_KIND.get(str(run_kind))
    if block.get("split_evaluated") != expected_split:
        problems.append(f"split_evaluated must be {expected_split!r} on a {run_kind} run")
    _check_conditional_fields(block, run_kind, problems)
    _check_completed_scalars(block, problems)
    _check_sets(block, problems)
    _check_gate_lists(block, problems)
    hidden_gates = check_criteria(
        block, strict=strict, split_evaluated=str(block.get("split_evaluated")), problems=problems
    )
    check_gates(
        block,
        strict=strict,
        hidden_gates=hidden_gates,
        require_criteria=not strict,
        problems=problems,
    )


def check_aborted(block: Mapping[str, object], projection: str, problems: list[str]) -> None:
    """Check the aborted-run shape of §3.3/§16.14 under the given projection.

    Permissive about extra identity fields — a run aborted after step 3 legitimately carries
    release identity a run aborted at step 3 does not. Under `projection="stdout"` on a HIDDEN
    run kind the §3.4 whitelist applies to the aborted shape too (§16.16d): only the whitelist
    plus `ABORTED_STDOUT_FIELDS` may be present, `split_evaluated` is REQUIRED (§16.16p),
    hidden-evidence gates project to their status alone, and the evaluated hidden split's
    criteria entries must already be collapsed.
    """
    strict = projection == "stdout" and block.get("run_kind") in HIDDEN_RUN_KINDS
    for field in ABORTED_FIELDS:
        if field not in block:
            problems.append(f"missing {field}")
    if block.get("status") != ERROR:
        problems.append("an aborted block has status ERROR")
    if not (isinstance(block.get("reason"), str) and block.get("reason")):
        problems.append("an aborted block states a non-empty reason")
    if not _is_int(block.get("aborted_at_step")):
        problems.append("aborted_at_step must be the §3.2 step number")
    for field in HASH_FIELDS:
        if field in block:
            _require_hex(field, block[field], nullable=True, problems=problems)
    for field in (*HIDDEN_BUDGET_FIELDS, "validation"):
        if field in block:
            problems.append(f"{field} is absent (never null) on an aborted run")
    if strict:
        if not (isinstance(block.get("split_evaluated"), str) and block["split_evaluated"]):
            problems.append("split_evaluated is required on an aborted hidden stdout block")
        outside = set(block) - STDOUT_FIELDS - ABORTED_STDOUT_FIELDS
        if outside:
            problems.append(f"fields outside the §3.4 stdout whitelist: {sorted(outside)}")
    hidden_gates = check_criteria(
        block,
        strict=strict,
        split_evaluated=str(block.get("split_evaluated")),
        problems=problems,
    )
    check_gates(
        block, strict=strict, hidden_gates=hidden_gates, require_criteria=False, problems=problems
    )


def collect_violations(block: Mapping[str, object], projection: str) -> list[str]:
    """Return every §3.3/§3.4 violation of `block` under `projection` (empty when valid)."""
    problems: list[str] = []
    check_envelope(block, problems)
    if block.get("aborted") is True:
        check_aborted(block, projection, problems)
    else:
        check_completed(block, projection, problems)
    return problems
