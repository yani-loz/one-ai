"""
Role: The shapes every gate evaluator is written against — the read-only `GateContext` handed to
      `evaluate`, the `GateResult` it returns, the per-case `CaseVerdict` of contract §16.11 — and
      the writers every evaluator shares: the §3.4 criteria entry, the §3.4/§4.5 status decision,
      the all-`incomplete` entry list an absent-component gate returns, and the
      `gates/<GATE>.json` report file of §16.13.
Used by: `tools.mem01_verify.gates.registry`, every `tools.mem01_verify.gates.gate_<name>` module
      and the runner `verify_step1`; sealed by `tests/tools/mem01_verify/test_gates_registry.py`
      and `test_gate_scoring.py`.
Depends on: `tools.mem01_verify.criteria` (the `Criterion` record a criteria entry is built from)
      and `.statuses` (the status vocabulary). The heavyweight wave-2 types (`ReleaseInfo`,
      `CorpusIdentity`, `ProbeSessions`, `AsyncSession`) are referenced under `TYPE_CHECKING`
      only, so importing this module never opens a database driver.
Key invariants:
  - `GateContext`, `GateResult` and `CaseVerdict` are FROZEN: an evaluator may not mutate the run.
  - A criteria entry always carries every §3.4 field; `acceptance_state` is `provisional` for
    every entry a non-`--validation` run writes, and an entry whose criterion is sourced from the
    validation split is `pending`, never scored (§3.4).
  - A ratio entry that is not decided carries `numerator = denominator = None`; a decided ratio
    below its `minimum` is ERROR, never a silent PASS (R2) — `criterion_status` owns that call,
    including the `errors > 0 → ERROR` rule (§16.16m): no gate re-derives it locally.
  - `write_gate_report` writes the ONLY artifact carrying per-case rows; `GateResult` deliberately
    has no `cases` field, so nothing case-level can leak into the machine block (R5).

Orchestrator determinations D1–D5 (2026-09-06) — the wave-3 build contract every evaluator and
the CLI builder obey; they are recorded here because this module is what every gate imports:
  D1. `evaluate(ctx) -> GateResult` and `registry.evaluate_all(ctx, only)` are ASYNC. `evaluate_all`
      returns all 17 keys in `GATE_NAMES` order; a gate outside `only` is `skipped`; an evaluator
      that raises becomes a gate `ERROR` whose reason names the exception TYPE only, never its
      text (R5); gate modules are imported lazily.
  D2. Each gate emits the COMPLETE annex entry set for its own gate — `PASS`/`FAIL`/`ERROR`/
      `incomplete`, and `pending` for every criterion whose `split_source == "validation"` — built
      through `criterion_entry` / `incomplete_entries`. The registry and the runner add NO entries:
      `{entry id} == set(annex ids)` is each gate's own obligation.
  D3. `GateResult.exclusions` carries the COV `{id, reason, policy_ref}` items; the runner
      concatenates them in `GATE_NAMES` order into `block["exclusions"]`. `diagnostics` stays
      numbers-only (§3.3) — an exclusion list never goes there.
  D4. `criterion_status` is the ONE implementation of the §3.4/§4.5 decision. A gate never
      re-derives PASS/FAIL/ERROR locally; passing `status=None` to `criterion_entry` derives it
      from `numerator`, `denominator` AND `errors` (§16.16m), so a gate that counted scoring
      failures passes `errors=` and carries no `if errors: ERROR` override of its own.
  D5. The baseline run evaluates the BIG org of the probe corpus, and the runner owns a SEPARATE
      fixture probe `mem01_probe_<run_id>` that it preflights and drops: `ctx.probe` is non-None
      whenever a fixture gate is selected, `ctx.corpus_snapshot` is the R6 corpus plane.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from tools.mem01_verify.criteria import CriteriaFile, Criterion
from tools.mem01_verify.exceptions import CriteriaError
from tools.mem01_verify.statuses import ERROR, FAIL, INCOMPLETE, PASS, PENDING

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager

    from sqlalchemy.ext.asyncio import AsyncSession

    from tools.mem01_verify.corpus_identity import CorpusIdentity
    from tools.mem01_verify.db import ProbeSessions
    from tools.mem01_verify.lock import ReleaseInfo

#: The acceptance state every entry outside a `--validation` run carries (§3.4).
PROVISIONAL = "provisional"
#: The split source whose criteria are only ever scored on the founder validation run.
VALIDATION_SPLIT = "validation"
#: The reason a validation-split criterion carries while it waits for the founder run.
PENDING_REASON = "validation-split criterion: scored only on the founder validation run"


@dataclass(frozen=True)
class GateContext:
    """Everything an evaluator may read; it owns no mutable run state (§1.4)."""

    release: ReleaseInfo
    criteria: CriteriaFile
    run_kind: str
    split_evaluated: str
    org_id: UUID
    corpus: CorpusIdentity | None
    # A zero-argument factory returning the R6 read-only snapshot context manager (§16.2), or
    # None when this run never opened the configured corpus.
    corpus_snapshot: Callable[[], AbstractAsyncContextManager[AsyncSession]] | None
    probe: ProbeSessions | None
    fixtures_digest: str
    report_dir: Path
    hidden_root: Path | None
    versions: Mapping[str, str]


@dataclass(frozen=True)
class GateResult:
    """One gate's verdict: its status, why, its §3.4 entries, aggregates and report files."""

    name: str
    status: str
    reason: str
    # Criteria entries are §3.4 JSON objects of mixed value types; `object` is the honest value.
    criteria: tuple[Mapping[str, object], ...] = ()
    # Diagnostics are free-form aggregates (ints, strings, lists of counted keys).
    diagnostics: Mapping[str, object] = field(default_factory=dict)
    report_files: tuple[Path, ...] = ()
    # D3: the block's `exclusions` rows this gate contributes — `{id, reason, policy_ref}` items
    # (COV, §4.6). The runner concatenates them in GATE_NAMES order; they never go in diagnostics.
    exclusions: tuple[Mapping[str, str], ...] = ()


@dataclass(frozen=True)
class CaseVerdict:
    """One scored fixture or probe case (§16.11); `defects` is non-empty exactly when it failed."""

    case_id: str
    criterion_id: str
    passed: bool
    defects: tuple[str, ...]


def _compare(value: float, operator: str, threshold: float) -> bool:
    """Apply one of the three §4.5 comparison operators; anything else is a criteria defect."""
    if operator == "==":
        return value == threshold
    if operator == "<=":
        return value <= threshold
    if operator == ">=":
        return value >= threshold
    raise CriteriaError(f"unsupported criterion operator {operator!r}")


def criterion_status(
    criterion: Criterion, *, numerator: int | None, denominator: int | None, errors: int = 0
) -> str:
    """Decide one criterion's status from its measured components (§3.4/§4.5, D4).

    This is the ONE implementation of the decision: an evaluator supplies the two measured
    numbers and never re-derives PASS / FAIL / ERROR itself.

    Args:
        criterion: The annex record carrying `kind`, `operator`, `threshold` and `minimum`.
        numerator: The measured numerator. `None` means the criterion could not be measured,
            which is ERROR — a criterion reaching this function is being DECIDED.
        denominator: The measured denominator for a `ratio` criterion; ignored for a `count`
            criterion, which has none.
        errors: How many inputs errored while being scored. Any positive count is ERROR (R2,
            §16.16m), decided BEFORE the ratio, minimum, zero-denominator and count rules.

    Returns:
        `PASS`, `FAIL` or `ERROR`.

    Edge cases:
        A mandatory `ratio` with a zero or missing denominator is ERROR regardless of `minimum`
        — there is no vacuous pass (§3.4). A denominator below the annex `minimum` is ERROR, so
        an error can never shrink a denominator into a cheaper pass (R2). Use `criterion_entry`
        with an explicit `incomplete` / `pending` status for criteria that are NOT being decided.
    """
    if errors > 0:
        return ERROR
    if numerator is None:
        return ERROR
    if criterion.kind == "ratio":
        if denominator is None or denominator == 0:
            return ERROR
        if criterion.minimum is not None and denominator < criterion.minimum:
            return ERROR
        value = numerator / denominator
    else:
        value = float(numerator)
    return PASS if _compare(value, criterion.operator, criterion.threshold) else FAIL


def criterion_entry(
    criterion: Criterion,
    *,
    status: str | None = None,
    reason: str,
    numerator: int | None = None,
    denominator: int | None = None,
    expected: int = 0,
    evaluated: int = 0,
    skipped: int = 0,
    errors: int = 0,
    versions: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Build the complete §3.4 criteria entry for one criterion.

    Args:
        criterion: The annex record the entry reports on; it supplies every frozen field
            (gate, set, split source, kind, operator, threshold, minimum, flags).
        status: One of the §3.4 criterion statuses, or `None` (the default) to DERIVE the status
            from `numerator` / `denominator` through `criterion_status` (D4). Pass it explicitly
            only for a criterion that is not being decided (`incomplete`, `pending`, `skipped`,
            `N/A`) or that the caller has already decided through `criterion_status`.
        reason: Why the criterion carries that status; never empty, never personal data (R5).
        numerator: The measured numerator, or None when the criterion was not decided.
        denominator: The measured denominator, or None for a `count` criterion and for an
            undecided ratio.
        expected: Inputs the criterion expected to score.
        evaluated: Inputs actually scored.
        skipped: Inputs deliberately not scored.
        errors: Inputs whose scoring errored (R2: these never shrink the denominator). A
            positive count makes a DERIVED status `ERROR` (§16.16m); when `status` is passed
            explicitly the count is recorded in the entry and decides nothing.
        versions: Component name → version for the measured components involved; an absent
            component contributes nothing, so the default is an empty mapping.

    Returns:
        A JSON-serializable dict carrying every field of §3.4; the result block renders it with
        sorted keys, so the key order here is irrelevant.
    """
    decided = (
        criterion_status(criterion, numerator=numerator, denominator=denominator, errors=errors)
        if status is None
        else status
    )
    return {
        "id": criterion.id,
        "gate": criterion.gate,
        "set": criterion.set,
        "split": criterion.split_source,
        "evidence_basis": criterion.evidence_basis,
        "acceptance_state": PROVISIONAL,
        "kind": criterion.kind,
        "numerator": numerator,
        "denominator": denominator,
        "denominator_def": criterion.denominator_def,
        "operator": criterion.operator,
        "threshold": criterion.threshold,
        "minimum": criterion.minimum,
        "status": decided,
        "reason": reason,
        "expected": expected,
        "evaluated": evaluated,
        "skipped": skipped,
        "errors": errors,
        "diagnostic_only": criterion.diagnostic_only,
        "directional": criterion.directional,
        "versions": dict(versions or {}),
    }


def incomplete_entries(criteria: Sequence[Criterion], reason: str) -> tuple[dict[str, object], ...]:
    """Build the entry list of a gate whose measured component does not exist yet (R3).

    Args:
        criteria: The gate's criteria, in annex order.
        reason: Why no criterion of this gate can be scored in this stage.

    Returns:
        One entry per criterion: `pending` for a criterion sourced from the validation split
        (it is only ever scored on the founder run, §3.4), `incomplete` for every other. Never
        `PASS` — an absent measured component prints `incomplete` (R3).
    """
    entries: list[dict[str, object]] = []
    for criterion in criteria:
        pending = criterion.split_source == VALIDATION_SPLIT
        entries.append(
            criterion_entry(
                criterion,
                status=PENDING if pending else INCOMPLETE,
                reason=PENDING_REASON if pending else reason,
            )
        )
    return tuple(entries)


def write_gate_report(
    report_dir: Path,
    *,
    name: str,
    status: str,
    reason: str,
    criteria: Sequence[Mapping[str, object]],
    cases: Sequence[CaseVerdict],
    diagnostics: Mapping[str, object],
) -> Path:
    """Write `<report_dir>/gates/<GATE>.json` with the §16.13 keys and return its path.

    Args:
        report_dir: The run's report directory (under the release for a tuning run, under the
            hidden root for a hidden run).
        name: The gate name, used verbatim as the file stem.
        status: The gate's status.
        reason: The gate's reason.
        criteria: The gate's §3.4 criteria entries.
        cases: Per-case verdicts for VISIBLE evidence; empty on hidden splits (§16.13).
        diagnostics: The gate's aggregates.

    Returns:
        The path written.

    Edge cases:
        The `gates/` subdirectory is created on demand; an existing file is replaced, because a
        gate is evaluated at most once per run.
    """
    directory = report_dir / "gates"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    payload = {
        "gate": name,
        "status": status,
        "reason": reason,
        "criteria": list(criteria),
        "cases": [dataclasses.asdict(case) for case in cases],
        "diagnostics": dict(diagnostics),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8"
    )
    return path
