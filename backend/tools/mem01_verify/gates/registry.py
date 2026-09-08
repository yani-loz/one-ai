"""
Role: The gate roster of contract §1.3 — the 17 gate names in the frozen order, the H-split and
      holdout tuples the verdict line and the hidden budget are written against — and
      `evaluate_all`, which drives one evaluator per gate and turns any evaluator failure into a
      gate `ERROR` instead of an aborted run (R2).
Used by: the runner `verify_step1` and `tools.mem01_verify.runner_steps` (which asks `probe_gates`
      whether the selected gates need a probe database) and `tools.mem01_verify.result_block`
      consumers; sealed by `tests/tools/mem01_verify/test_gates_registry.py`.
Depends on: `tools.mem01_verify.statuses` (the status vocabulary and the same rosters, which this
      module cross-checks at import), `.gates.context` (the context and result shapes) and, lazily
      at call time, the 17 `gates.gate_<name>` modules. `probe_gates` reads the `NEEDS_PROBE` flag
      each of those modules declares, so the runner never hardcodes a copy of that roster.
Key invariants:
  - `GATE_NAMES` is exactly 17 names in the frozen order; `HOLDOUT_GATES` is the order the verdict
    line prints `provisional=` in. Both are declared here literally and mechanically checked
    against `statuses` at import — a drift between the two rosters is an integrity error, not a
    silent disagreement.
  - Every `gates.gate_<name>` module declares `NEEDS_PROBE: bool`; `probe_gates` is the ONLY
    place that roster is assembled, and a module that declares no boolean flag is an integrity
    error rather than a silently probe-less gate.
  - `evaluate_all` returns one entry for EVERY gate name, in roster order, whatever `only` says;
    a gate outside `only` is `skipped` (which makes the block ERROR under §3.5), never absent.
  - An evaluator that raises yields a gate `ERROR` whose reason names the exception CLASS only —
    an exception message can echo row values, and stdout carries no personal data (R5); the
    traceback goes to the protected `gates/<GATE>.json` report file, never to stdout. The one
    exception that PROPAGATES is `ProbeDatabaseError`: the §12 targeting boundary aborts the run.
  - Evaluator modules are imported lazily inside `evaluate_all`, so the roster is importable
    independently of any single evaluator.
"""

from __future__ import annotations

import importlib
import inspect
import traceback

from tools.mem01_verify import statuses
from tools.mem01_verify.exceptions import IntegrityViolationError, ProbeDatabaseError
from tools.mem01_verify.gates.context import (
    GateContext,
    GateResult,
    criterion_entry,
    write_gate_report,
)

#: The 17 gates, in the frozen contract order (§1.3).
GATE_NAMES: tuple[str, ...] = (
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

#: The gates whose evidence is labeled H data split three ways (§3.6 charges the budget per set).
H_SPLIT_GATES: tuple[str, ...] = ("QS", "NF", "LANG", "RET")

#: The holdout gates, in the frozen order the verdict line renders `provisional=` in (§3.8).
HOLDOUT_GATES: tuple[str, ...] = ("FID", "THR", "IDENT", "ATTR")

_MODULE_PREFIX = "tools.mem01_verify.gates.gate_"
_SKIPPED_REASON = "not selected by --gates"


def _assert_rosters_agree() -> None:
    """Refuse to import when this roster and `statuses` disagree (the mechanical guard).

    Raises:
        IntegrityViolationError: A name, an order or a tuple differs between the two modules.
    """
    pairs = (
        ("GATE_NAMES", GATE_NAMES, statuses.GATE_NAMES),
        ("H_SPLIT_GATES", H_SPLIT_GATES, statuses.H_SPLIT_GATES),
        ("HOLDOUT_GATES", HOLDOUT_GATES, statuses.HOLDOUT_GATES),
    )
    for label, here, there in pairs:
        if here != there:
            raise IntegrityViolationError(
                f"gate roster {label} disagrees between gates.registry and statuses: "
                f"{here} != {there}"
            )
    if len(set(GATE_NAMES)) != 17:
        raise IntegrityViolationError(f"GATE_NAMES must hold 17 distinct names, got {GATE_NAMES}")


_assert_rosters_agree()


def probe_gates() -> frozenset[str]:
    """The gates that need the run's probe database, read from each module's `NEEDS_PROBE` (§12).

    The flag lives beside the evaluator that uses `ctx.probe`, so adding a probe-backed gate can
    never leave the runner leasing no probe for it. Modules are imported here, at call time, to
    keep the roster importable independently of any evaluator.

    Returns:
        The subset of `GATE_NAMES` whose module declares `NEEDS_PROBE = True`.

    Raises:
        IntegrityViolationError: A gate module declares no `NEEDS_PROBE`, or declares one that is
            not a `bool` — the probe roster cannot be established, so the run must not proceed.
    """
    needing: list[str] = []
    for name in GATE_NAMES:
        module = importlib.import_module(f"{_MODULE_PREFIX}{name.lower()}")
        flag = getattr(module, "NEEDS_PROBE", None)
        if not isinstance(flag, bool):
            raise IntegrityViolationError(
                f"gate module for {name} declares no boolean NEEDS_PROBE (got {flag!r})"
            )
        if flag:
            needing.append(name)
    return frozenset(needing)


def _skipped_result(ctx: GateContext, name: str) -> GateResult:
    """Return the `skipped` result of a gate `--gates` left out, with every entry `skipped`."""
    entries = tuple(
        criterion_entry(criterion, status=statuses.SKIPPED, reason=_SKIPPED_REASON)
        for criterion in ctx.criteria.by_gate.get(name, ())
    )
    return GateResult(name=name, status=statuses.SKIPPED, reason=_SKIPPED_REASON, criteria=entries)


def _error_result(ctx: GateContext, name: str, exc: Exception) -> GateResult:
    """Build the gate `ERROR` result of a failed evaluator, keeping the block schema complete.

    Every criterion of the gate gets an `ERROR` entry, so the block's criteria list still equals
    the annex id set and the gate's `errors` counter has rows to count (R2). The exception TEXT
    goes to the protected report file only; stdout carries the class name (R5).
    """
    reason = f"evaluator raised {type(exc).__name__}"
    entries = tuple(
        criterion_entry(criterion, status=statuses.ERROR, reason=reason, errors=1)
        for criterion in ctx.criteria.by_gate.get(name, ())
    )
    diagnostics = {
        "exception": type(exc).__name__,
        "traceback": "".join(traceback.format_exception(exc)),
    }
    report = write_gate_report(
        ctx.report_dir,
        name=name,
        status=statuses.ERROR,
        reason=reason,
        criteria=entries,
        cases=(),
        diagnostics=diagnostics,
    )
    return GateResult(
        name=name,
        status=statuses.ERROR,
        reason=reason,
        criteria=entries,
        diagnostics={"exception": type(exc).__name__},
        report_files=(report,),
    )


async def _evaluate_one(ctx: GateContext, name: str) -> GateResult:
    """Import and run one gate's evaluator, converting any failure into a gate `ERROR` (R2).

    Args:
        ctx: The run context handed to the evaluator unchanged.
        name: The gate name; its module is `gates.gate_<lowercase name>`.

    Returns:
        The evaluator's `GateResult`, or an `ERROR` result naming only the exception class.

    Raises:
        ProbeDatabaseError: The evaluator hit the §12 targeting safety boundary. That is never
            one gate's problem — the run aborts (exit 2, no verdict line) rather than letting the
            remaining 16 gates keep opening sessions against a wrongly bound database.
    """
    try:
        module = importlib.import_module(f"{_MODULE_PREFIX}{name.lower()}")
        result = module.evaluate(ctx)
        if inspect.isawaitable(result):
            result = await result
    except ProbeDatabaseError:
        raise
    except Exception as exc:  # noqa: BLE001 - R2: an evaluator failure is a gate ERROR, not a crash
        return _error_result(ctx, name, exc)
    return result


async def evaluate_all(ctx: GateContext, only: frozenset[str] | None) -> dict[str, GateResult]:
    """Evaluate every gate of the roster and return the results keyed by gate name.

    Args:
        ctx: The run context; evaluators read it and never mutate it.
        only: The gate names `--gates` selected, or None for the whole roster. Names outside
            the roster are the runner's business (§16.10 aborts the run before this point).

    Returns:
        A dict with one entry per name of `GATE_NAMES`, inserted in roster order. A gate outside
        `only` carries `skipped`; a gate whose evaluator raised carries `ERROR`.
    """
    results: dict[str, GateResult] = {}
    for name in GATE_NAMES:
        if only is not None and name not in only:
            results[name] = _skipped_result(ctx, name)
            continue
        results[name] = await _evaluate_one(ctx, name)
    return results
