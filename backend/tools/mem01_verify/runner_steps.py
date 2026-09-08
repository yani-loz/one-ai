"""
Role: The observable steps of the run sequence of contract §3.2, one function per step —
      stage-1 lock and criteria load (step 3), the R6 corpus snapshot with `CorpusIdentity` and
      the visible roster (step 4), the database identity, the closure and the hashes (step 5),
      the §16.13 scorability check with the hidden reservation or the validation admission
      (step 6), stage-2 lock and hidden roster (step 7), gate evaluation (step 8), the
      input-observer check (step 9) and the step-12 journal record of a printed founder verdict.
      Where the run writes and which probe it borrows is `runner_probe`; the CLI in
      `verify_step1` calls both in order and owns every line reaching stdout.
Used by: `tools.mem01_verify.verify_step1`; sealed through the CLI by
      `tests/tools/mem01_verify/test_verify_step1_*.py` and `test_gates_stage_a.py`, and at
      module level by `test_hidden_budget_display.py` and `test_observer_scope.py`.
Depends on: `tools.mem01_verify.lock`, `.roster`, `.criteria`, `.db`, `.corpus_identity`,
      `.run_identity`, `.hidden_budget`, `.validation_guard`, `.statuses`, `.gates.registry`,
      `.gates.context`, `.fixtures.digest`, `.exceptions`, `.runner_output` and `.runner_probe`.
      The report directory's own artifacts are written by `.runner_logging`.
Key invariants:
  - R6: ONE snapshot session is opened at step 4 and every corpus gate reads through it — the
    factory handed to `GateContext` yields that same session and never opens a second one.
  - R1: the hidden root is untouched before step 6 authorized the split, and the split's files
    are opened only in step 7, only for the SETs step 6 reserved.
  - §3.11's window is the LITERAL step 2 → step 9 window (§16.16(a)-(c)): nothing here suspends
    the observer. The Alembic child gets an allowlist-only environment, the driver's `PG*` reads
    are allowlisted, and pinned dependency content under `backend/.venv/` is never an offender —
    so every read the window records is one the closure really has to account for.
  - Nothing here prints, so no step can emit a stray line before the machine block.
"""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tools.mem01_verify import corpus_identity, db, lock, roster, run_identity
from tools.mem01_verify.criteria import load_criteria
from tools.mem01_verify.exceptions import (
    IntegrityViolationError,
    ReleaseLockError,
    RosterMismatchError,
    RunRefusedError,
)
from tools.mem01_verify.fixtures.digest import fixtures_digest
from tools.mem01_verify.gates.context import GateContext
from tools.mem01_verify.gates.registry import evaluate_all
from tools.mem01_verify.hidden_budget import HiddenBudget, split_digest
from tools.mem01_verify.run_identity import Closure, InputObserver
from tools.mem01_verify.runner_output import HIDDEN_RUN_KINDS, RunState
from tools.mem01_verify.runner_probe import REPO_ROOT
from tools.mem01_verify.statuses import GATE_NAMES, H_SPLIT_GATES

#: §16.10: the literal refusal reason of a `--gates` name outside the frozen 17.
UNKNOWN_GATE_REASON = "unknown gate: {name}"
#: §16.13: the literal refusal reason when no H-split gate can score the hidden split.
NO_SCORABLE_REASON = "no scorable hidden set"
#: §16.13: a partial run is never an acceptance path, so it never reserves and is never admitted.
PARTIAL_HIDDEN_REASON = "partial run (--gates) is not an acceptance path"
#: §3.1/§16.16(e): the literal refusal reason of a frozen release invoked without `--expect-lock`.
EXPECT_LOCK_REASON = "expect-lock required on a frozen release"
AUDIT_FILENAME = "audit.jsonl"
DEFAULT_LEDGER_NAME = "hidden_budget.jsonl"
VALIDATION_PRINCIPAL = "founder"

_GATE_MODULE_PREFIX = "tools.mem01_verify.gates.gate_"


def selected_gates(gates_option: str | None) -> frozenset[str] | None:
    """Parse the `--gates` subset of §3.1; None when the whole roster runs.

    Raises:
        RunRefusedError: a name is not one of the 17 gates (§16.10 refusal reason).
    """
    if not gates_option:
        return None
    names = [name.strip() for name in gates_option.split(",") if name.strip()]
    for name in names:
        if name not in GATE_NAMES:
            raise RunRefusedError(UNKNOWN_GATE_REASON.format(name=name))
    return frozenset(names)


def verify_release(state: RunState, *, release_dir: Path, expect_lock: str | None) -> None:
    """Step 3: stage-1 lock verification, the hidden-run release-state rule, and the annex.

    A frozen release is refused outright when the caller named no `--expect-lock` (§3.1 line
    161, §16.16(e)): the refusal lands here, at step 3, so nothing downstream — no roster, no
    reservation, no admission, no stale-probe listing and no maintenance connection — has run.

    Raises:
        ReleaseLockError: the manifest, a visible file, the expected lock or the release state
            does not admit this run, or a frozen release was invoked without `--expect-lock`.
        RunnerHashMismatchError: a frozen release froze a different `runner_sha256` (R7).
        CriteriaError: the annex the release carries is not loadable.
    """
    release = lock.verify_release_visible(release_dir, expect_lock=expect_lock)
    state.release = release
    if release.state == "frozen" and expect_lock is None:
        raise ReleaseLockError(EXPECT_LOCK_REASON)
    if state.run_kind in HIDDEN_RUN_KINDS and release.state != "frozen":
        raise ReleaseLockError(
            f"--{state.run_kind} requires a frozen release; this release is {release.state}"
        )
    state.criteria = load_criteria(release.criteria_path)


def manifest_org_id(state: RunState) -> UUID:
    """The corpus org the manifest names — the default for `--org`."""
    corpus = state.require_release().manifest.get("corpus")
    if not isinstance(corpus, Mapping) or not isinstance(corpus.get("org_id"), str):
        raise ReleaseLockError("release manifest carries no usable corpus.org_id")
    return UUID(str(corpus["org_id"]))


async def identify_corpus(state: RunState, session: AsyncSession, org_id: UUID) -> None:
    """Step 4: compute `CorpusIdentity`, verify the visible roster, pin the manifest's corpus.

    Raises:
        RosterMismatchError: a visible record is missing, duplicated or unexpected, or the
            measured corpus digest differs from the one the manifest was cut against — the shape
            an `--org` off the manifest takes (§16.10).
    """
    release = state.require_release()
    state.corpus = await corpus_identity.corpus_digest(session, org_id)
    roster.verify_roster(release, split="optimization", hidden_root=None)
    declared = release.manifest.get("corpus")
    expected = declared.get("corpus_digest") if isinstance(declared, Mapping) else None
    if expected != state.corpus.corpus_digest:
        raise RosterMismatchError(
            "the measured corpus digest differs from the one the manifest was cut against — the "
            "corpus roster this release declares by digest cannot be established"
        )


async def read_database_identity(state: RunState, session: AsyncSession) -> None:
    """Step 5 (database half): the migrations digest and the two server-side version keys."""
    state.migrations_digest = await run_identity.migrations_digest(session)
    state.versions = {**run_identity.versions(), **(await run_identity.db_versions(session))}


def build_identity(state: RunState, *, cli_options: Mapping[str, object]) -> Closure:
    """Step 5 (file half): the closure over the editable scope and the two candidate hashes."""
    state.fixtures_digest = fixtures_digest()
    closure = run_identity.build_closure(
        REPO_ROOT,
        state.require_criteria(),
        corpus=state.require_corpus(),
        migrations_digest=str(state.migrations_digest),
        fixtures_digest=state.fixtures_digest,
        cli_options=cli_options,
    )
    state.code_hash = run_identity.code_hash(closure)
    state.config_hash = run_identity.config_hash(closure)
    return closure


def scorable_hidden_sets() -> tuple[str, ...]:
    """The H-split SETs whose gate can score the hidden split today (§16.13)."""
    scorable: list[str] = []
    for name in H_SPLIT_GATES:
        module = importlib.import_module(f"{_GATE_MODULE_PREFIX}{name.lower()}")
        if module.hidden_scorable():
            scorable.append(name)
    return tuple(scorable)


def admit_hidden_run(state: RunState, *, hidden_root: Path) -> tuple[str, ...]:
    """Step 6: the §16.13 scorability check, then the reservation or the admission.

    Returns:
        The SETs this run is authorized to score on the hidden split.

    Raises:
        RunRefusedError: no H gate can score the split, or the run is partial — this invocation
            is not an acceptance path at all.
        IntegrityViolationError: the hidden root is absent. Together with the two refusals above
            this happens BEFORE any reservation, admission or hidden read, so no unit is charged
            and no attempt is recorded.
        HiddenBudgetExhaustedError: a selected split is at its effective limit (§3.6).
        HiddenBudgetLedgerError: the required ledger is missing or unreadable.
        ValidationRefusedError: the audit journal does not admit this founder run (§3.7).
    """
    scorable = scorable_hidden_sets()
    if not scorable:
        raise RunRefusedError(NO_SCORABLE_REASON)
    if state.partial:
        raise RunRefusedError(PARTIAL_HIDDEN_REASON)
    if not hidden_root.is_dir():
        raise IntegrityViolationError("the hidden root is absent; no hidden split can be scored")
    if state.run_kind == "checkpoint":
        _reserve_hidden_budget(state, scorable, hidden_root)
    else:
        _admit_validation(state)
    return scorable


def hidden_display_digests(manifest: Mapping[str, object]) -> dict[str, str]:
    """The split digest of ALL FOUR H splits, for the §3.6 display (§16.16(f)).

    The bracket and `hidden_budget_by_split` report the cumulative counter of every H split,
    including the splits a run never reserved, so a reader sees the whole holdout's spend — and
    each counter is keyed by that split's OWN manifest digest, never by the run's selection.

    Args:
        manifest: The release manifest, whose `files` entries key every digest.

    Returns:
        SET name → `split_digest(manifest, name)` for `QS, NF, LANG, RET`.

    Raises:
        HiddenBudgetLedgerError: the manifest cannot key one of the four splits.
    """
    return {name: split_digest(manifest, name) for name in H_SPLIT_GATES}


def _reserve_hidden_budget(state: RunState, scorable: Sequence[str], hidden_root: Path) -> None:
    """Charge one unit against every selected split, durably, before any hidden file opens.

    The reservation names exactly the scorable splits; the counters the block and the verdict
    display are read over all four (§16.16(f)). The budget is built with the HIDDEN root as its
    `results_root`, so a completed attempt's protected result is cached under the holdout and
    never beside the visible ledger (§16.17(h)); the caller has already proved that root is a
    directory, so nothing is written under an absent hidden root.
    """
    release = state.require_release()
    ledger_name = str(release.manifest.get("budget_ledger") or DEFAULT_LEDGER_NAME)
    budget = HiddenBudget(release.path.parent.parent / ledger_name, results_root=hidden_root)
    display = hidden_display_digests(release.manifest)
    state.reservation = budget.reserve(
        lock_sha256=release.lock_sha256,
        split_digests={name: display[name] for name in scorable},
        code_hash=str(state.code_hash),
        config_hash=str(state.config_hash),
        run_id=state.run_id,
    )
    state.budget = budget
    state.hidden = budget.counters(display, lock_sha256=release.lock_sha256)


def _admit_validation(state: RunState) -> None:
    """Record the founder admission of §3.7 before any hidden file is opened."""
    from tools.mem01_verify import validation_guard

    release = state.require_release()
    audit_path = release.path / AUDIT_FILENAME
    candidate = {"code_hash": str(state.code_hash), "config_hash": str(state.config_hash)}
    validation_guard.check_validation_preconditions(
        audit_path, lock_sha256=release.lock_sha256, principal=VALIDATION_PRINCIPAL, **candidate
    )
    state.attempt_id = validation_guard.record_admission(
        audit_path,
        lock_sha256=release.lock_sha256,
        principal=VALIDATION_PRINCIPAL,
        session=state.run_id,
        run_id=state.run_id,
        **candidate,
    )
    state.validation_complete = True


def record_verdict_printed(state: RunState, verdict: str) -> None:
    """Step 12: the founder run records that its verdict reached stdout (§3.7)."""
    if state.run_kind != "validation" or state.attempt_id is None:
        return
    from tools.mem01_verify import validation_guard

    audit = state.require_release().path / AUDIT_FILENAME
    validation_guard.record_verdict_printed(audit, state.attempt_id, verdict)


def verify_hidden_split(state: RunState, *, hidden_root: Path, sets: Sequence[str]) -> None:
    """Step 7: stage-2 lock and roster for exactly the selected split, never the other (§4.3)."""
    release = state.require_release()
    split = "test" if state.run_kind == "checkpoint" else "validation"
    lock.verify_hidden_split(release, hidden_root, split, sets)  # type: ignore[arg-type]
    roster.verify_roster(
        release,
        split=split,  # type: ignore[arg-type]  # one of the two hidden splits
        hidden_root=hidden_root,
        sets=sets,
    )


def shared_snapshot(session: AsyncSession) -> Callable[[], object]:
    """A zero-argument factory yielding the ONE R6 snapshot session of this run (§16.2)."""

    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield session

    return factory


async def evaluate_gates(
    state: RunState,
    *,
    session: AsyncSession,
    selected: frozenset[str] | None,
    hidden_root: Path | None,
) -> None:
    """Step 8: evaluate every gate against the run context and record the 17 results."""
    context = GateContext(
        release=state.require_release(),
        criteria=state.require_criteria(),
        run_kind=state.run_kind,
        split_evaluated=state.split_evaluated,
        org_id=state.require_corpus().org_id,
        corpus=state.corpus,
        corpus_snapshot=shared_snapshot(session),
        probe=db.probe_session_factories(state.probe_name) if state.probe_name else None,
        fixtures_digest=str(state.fixtures_digest),
        report_dir=state.require_report_dir(),
        hidden_root=hidden_root,
        versions=state.versions,
    )
    state.gate_results = await evaluate_all(context, selected)


def check_observer(state: RunState, observer: InputObserver, closure: Closure) -> None:
    """Step 9: list every read that fell outside the closure (§3.11).

    On a hidden run an offender aborts the run before any feedback is released (the reserved
    unit stays charged); on a tuning run it is recorded in the block and the report.

    Raises:
        IntegrityViolationError: a hidden run observed a read outside its closure.
    """
    state.opened_outside_closure = [str(item) for item in observer.check_within(closure)]
    if state.opened_outside_closure and state.run_kind in HIDDEN_RUN_KINDS:
        raise IntegrityViolationError(
            f"{len(state.opened_outside_closure)} read(s) fell outside the declared closure"
        )
