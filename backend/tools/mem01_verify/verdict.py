"""
Role: The verdict line of contract §3.8 — the frozen one-line grammar every completed run
      prints last, rendered by `format_verdict_line` and recovered by `parse_verdict_line`.
      Both directions enforce the SAME rules, so a line this module renders is a line it
      accepts and nothing else is.
Used by: the runner `verify_step1` (printing) and the sealed oracle / the baseline comparison
      (parsing); sealed by `tests/tools/mem01_verify/test_verdict.py`.
Depends on: `tools.mem01_verify.exceptions`, `.statuses` (the gate rosters) and `.run_id`
      (the single encoding of the §16.3 run-id grammar, spliced into the line pattern).
Key invariants:
  - Separator is exactly " | "; the per-split bracket separator is exactly " · ".
  - `hidden …` appears iff `run_kind == "checkpoint"`; `validation=complete` appears iff
    `run_kind == "validation"`, immediately before `directional=`; a TUNING line carries
    neither (§3.8, §16.10).
  - `0 <= passed <= 17`; the denominator is literally 17; `provisional` is a subsequence of
    `("FID","THR","IDENT","ATTR")` and `p` equals its length, on EVERY run kind (§16.12).
  - `hidden.total` equals `max(hidden.by_split.values())` and `by_split` carries exactly the
    four H-split keys, rendered in the frozen order QS, NF, LANG, RET.
  - Hashes are 64 lowercase hex characters; `run_id` matches the §16.3 grammar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from tools.mem01_verify.exceptions import VerdictFormatError
from tools.mem01_verify.run_id import RUN_ID_REGEX, is_run_id
from tools.mem01_verify.statuses import GATE_NAMES, H_SPLIT_GATES, HOLDOUT_GATES

SEPARATOR = " | "
MIDDLE_DOT = "·"
BRACKET_SEPARATOR = f" {MIDDLE_DOT} "
GATE_TOTAL = 17

_HEX = "[0-9a-f]{64}"
_GATE_LIST = "-|[A-Z]+(?:,[A-Z]+)*"
_BRACKET = (
    rf"\(QS (?P<qs>\d+){BRACKET_SEPARATOR}NF (?P<nf>\d+)"
    rf"{BRACKET_SEPARATOR}LANG (?P<lang>\d+){BRACKET_SEPARATOR}RET (?P<ret>\d+)\)"
)
_LINE = re.compile(
    rf"^STEP1 (?P<kind>TUNING|ACCEPTANCE): (?P<passed>\d{{1,2}})/{GATE_TOTAL} PASS"
    rf" \| provisional=(?P<pcount>\d+):(?P<plist>{_GATE_LIST})"
    rf"(?: \| hidden (?P<total>\d+)/(?P<limit>\d+) {_BRACKET})?"
    rf"(?: \| validation=(?P<validation>complete))?"
    rf" \| directional=(?P<dlist>{_GATE_LIST})"
    rf" \| run_id=(?P<run_id>{RUN_ID_REGEX})"
    rf" \| lock=sha256:(?P<lock>{_HEX})"
    rf" \| runner=sha256:(?P<runner>{_HEX})$"
)

_KIND_BY_RUN_KIND = {"tuning": "TUNING", "checkpoint": "ACCEPTANCE", "validation": "ACCEPTANCE"}


@dataclass(frozen=True)
class HiddenCounters:
    """Cumulative hidden-budget counters as of this run's reservation (§3.6).

    Attributes:
        total: The maximum over the four cumulative per-split counters.
        limit: The effective limit of the split that attains that maximum.
        by_split: Cumulative reservations per split digest, keyed `QS, NF, LANG, RET`.
        invocations_under_lock: Reservations recorded under this lock, any split.
    """

    total: int
    limit: int
    by_split: dict[str, int] = field(default_factory=dict)
    invocations_under_lock: int = 0


@dataclass(frozen=True)
class VerdictFields:
    """The parsed or to-be-rendered content of one verdict line (§3.8)."""

    run_kind: Literal["tuning", "checkpoint", "validation"]
    passed: int
    provisional: tuple[str, ...]
    directional: tuple[str, ...]
    run_id: str
    lock_sha256: str
    runner_sha256: str
    hidden: HiddenCounters | None = None
    validation_complete: bool = False


def _render_gate_list(gates: tuple[str, ...]) -> str:
    """Render a gate list as the grammar's `-` (empty) or comma-joined names without spaces."""
    return ",".join(gates) if gates else "-"


def _parse_gate_list(rendered: str) -> tuple[str, ...]:
    """Recover a gate list rendered by `_render_gate_list`."""
    return () if rendered == "-" else tuple(rendered.split(","))


def _is_subsequence(candidate: tuple[str, ...], frozen: tuple[str, ...]) -> bool:
    """True when `candidate` keeps the relative order of `frozen` and repeats nothing."""
    remaining = list(frozen)
    for name in candidate:
        if name not in remaining:
            return False
        remaining = remaining[remaining.index(name) + 1 :]
    return True


def _check_common(fields: VerdictFields) -> list[str]:
    """Return every rule violation shared by rendering and parsing."""
    problems: list[str] = []
    if fields.run_kind not in _KIND_BY_RUN_KIND:
        problems.append(f"unknown run_kind {fields.run_kind!r}")
    if not 0 <= fields.passed <= GATE_TOTAL:
        problems.append(f"passed {fields.passed} outside 0..{GATE_TOTAL}")
    if not _is_subsequence(fields.provisional, HOLDOUT_GATES):
        problems.append(f"provisional {fields.provisional} is not in the order {HOLDOUT_GATES}")
    unknown = [name for name in fields.directional if name not in GATE_NAMES]
    if unknown or len(set(fields.directional)) != len(fields.directional):
        problems.append(f"directional {fields.directional} is not a set of gate names")
    if not is_run_id(fields.run_id):
        problems.append(f"run_id {fields.run_id!r} does not match the §16.3 grammar")
    for label, digest in (("lock", fields.lock_sha256), ("runner", fields.runner_sha256)):
        if not re.fullmatch(_HEX, digest):
            problems.append(f"{label} is not 64 lowercase hex characters")
    problems.extend(_check_optional_fields(fields))
    return problems


def _check_optional_fields(fields: VerdictFields) -> list[str]:
    """Return violations of the hidden / validation presence and consistency rules."""
    problems: list[str] = []
    if (fields.hidden is not None) != (fields.run_kind == "checkpoint"):
        problems.append(f"the hidden field belongs to checkpoint runs only, not {fields.run_kind}")
    if fields.validation_complete != (fields.run_kind == "validation"):
        problems.append(
            f"validation=complete belongs to validation runs only, not {fields.run_kind}"
        )
    hidden = fields.hidden
    if hidden is None:
        return problems
    if tuple(hidden.by_split) != H_SPLIT_GATES:
        problems.append(f"hidden by_split keys must be exactly {H_SPLIT_GATES} in that order")
    elif hidden.total != max(hidden.by_split.values()):
        problems.append("hidden total is not the maximum of the per-split counters")
    return problems


def _refuse(problems: list[str], line: str | None) -> None:
    """Raise `VerdictFormatError` naming every problem, and the offending line when parsing."""
    if not problems:
        return
    suffix = f" | line: {line!r}" if line is not None else ""
    raise VerdictFormatError("verdict line violates §3.8: " + "; ".join(problems) + suffix)


def format_verdict_line(fields: VerdictFields) -> str:
    """Render the §3.8 verdict line for a completed run.

    Args:
        fields: The run's verdict content. `hidden` must be present exactly on checkpoint runs
            and `validation_complete` exactly on validation runs.

    Returns:
        The single verdict line, without a trailing newline.

    Raises:
        VerdictFormatError: Any deviation from §3.8 — an out-of-range `passed`, a provisional
            list off the frozen order, a stray or missing `hidden` / `validation` field, hidden
            counters whose total is not the per-split maximum, a malformed run id or hash.
    """
    _refuse(_check_common(fields), None)
    parts = [
        f"STEP1 {_KIND_BY_RUN_KIND[fields.run_kind]}: {fields.passed}/{GATE_TOTAL} PASS",
        f"provisional={len(fields.provisional)}:{_render_gate_list(fields.provisional)}",
    ]
    if fields.hidden is not None:
        counters = fields.hidden
        bracket = BRACKET_SEPARATOR.join(
            f"{split} {counters.by_split[split]}" for split in H_SPLIT_GATES
        )
        parts.append(f"hidden {counters.total}/{counters.limit} ({bracket})")
    if fields.validation_complete:
        parts.append("validation=complete")
    parts.append(f"directional={_render_gate_list(fields.directional)}")
    parts.append(f"run_id={fields.run_id}")
    parts.append(f"lock=sha256:{fields.lock_sha256}")
    parts.append(f"runner=sha256:{fields.runner_sha256}")
    return SEPARATOR.join(parts)


def _run_kind_of(kind: str, has_hidden: bool, has_validation: bool, line: str) -> str:
    """Derive the run kind from the printed kind word and the two optional fields."""
    if kind == "TUNING":
        if has_hidden or has_validation:
            _refuse(["a TUNING line carries neither the hidden nor the validation field"], line)
        return "tuning"
    if has_hidden == has_validation:
        _refuse(["an ACCEPTANCE line carries exactly one of hidden / validation=complete"], line)
    return "checkpoint" if has_hidden else "validation"


def parse_verdict_line(line: str) -> VerdictFields:
    """Recover the fields of a §3.8 verdict line, refusing every deviation.

    Args:
        line: One verdict line, without surrounding whitespace.

    Returns:
        The `VerdictFields` that `format_verdict_line` renders back to the same line.

    Raises:
        VerdictFormatError: The line does not match the grammar exactly (separators, field
            order, the frozen provisional order, the per-split bracket, lowercase hashes, the
            run-id form), or matches it but violates a consistency rule (`p` against the list
            length, `passed` bounds, the hidden total against the per-split maximum).
    """
    match = _LINE.match(line)
    if match is None:
        raise VerdictFormatError(f"verdict line violates §3.8: grammar mismatch | line: {line!r}")
    provisional = _parse_gate_list(match["plist"])
    if int(match["pcount"]) != len(provisional):
        _refuse([f"provisional count {match['pcount']} does not match the list"], line)
    hidden = None
    if match["total"] is not None:
        hidden = HiddenCounters(
            total=int(match["total"]),
            limit=int(match["limit"]),
            by_split={split: int(match[split.lower()]) for split in H_SPLIT_GATES},
            invocations_under_lock=0,
        )
    run_kind = _run_kind_of(match["kind"], hidden is not None, bool(match["validation"]), line)
    fields = VerdictFields(
        run_kind=run_kind,  # type: ignore[arg-type]  # narrowed by _run_kind_of
        passed=int(match["passed"]),
        provisional=provisional,
        directional=_parse_gate_list(match["dlist"]),
        run_id=match["run_id"],
        lock_sha256=match["lock"],
        runner_sha256=match["runner"],
        hidden=hidden,
        validation_complete=bool(match["validation"]),
    )
    _refuse(_check_common(fields), line)
    return fields
