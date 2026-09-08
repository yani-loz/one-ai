"""
Role: The MEM-01 step-1 runner's ENTRY POINT and step orchestration (contract §3) — both
      invocation forms of §3.1, the option set, the §3.2 sequence in its observable order, the
      §3.5 exit codes, the ONE R11 guard of §16.16(t), and everything that reaches stdout: the
      §3.9 self-test line, the §3.3 machine block, the per-SET lines of a hidden run and the
      single §3.8 verdict line of a completed run.
Used by: the /goal command, the §13 baseline pair, and the sealed oracle
      (`tests/tools/mem01_verify/test_verify_step1_*.py`, `test_entry_guard.py`).
Depends on: the standard library ALONE at module level (A25); inside the guard on
      `tools.mem01_verify.runner_steps`, `.runner_probe`, `.runner_cleanup`, `.runner_render`,
      `.runner_logging`, `.runner_output`, `.utf8_selftest`, `.statuses`, `.hashing`,
      `.run_identity`, `.exceptions`, `.db` and `app.core.config`.
Key invariants:
  - R8: stdout and stderr are UTF-8 before any other output; the self-test line is the FIRST line.
  - §16.16(t)/A25: ONE guard. `main` forces UTF-8 on both streams, then does everything else
    inside a single `try` — importing the runner (whose module bodies construct the application
    settings), the steps, `runner_sha256()`, the ledger and journal writes, the renders and the
    stream writes. Any exception becomes the best-effort line `MEM01 INTERNAL ERROR: <class>`
    (its own write failure suppressed) and exit 2; no traceback ever reaches stderr.
  - R11: an aborted run prints the machine block with `aborted: true` and NO verdict line; only a
    completed run prints the verdict, once, last, after the probe drop and audit finalization.
  - R5: nothing printed here carries personal data — a refusal reason is a `Mem01Error` message
    (written to be safe) or an exception CLASS name; from step 4 to step 11 the `app` logger
    hierarchy is held on EVERY run (§16.16(k)/(t)), in `<report dir>/app.log` or in the discard
    sink when no report directory may be written.
  - §16.16(d)/(t)/A21: EVERY run prints `project_for_stdout(block)` and only after the stdout
    schema accepted it; the printed shape — block text, per-SET lines and verdict line, as
    STRINGS — is decided BEFORE any outcome is recorded, so a run whose projection or render
    fails is recorded ABORTED, never completed and never verdict-reserved. The fallback is the
    minimal aborted block of §16.16(t), on the free-repeat path as well; if even that is refused,
    one `MEM01 INTERNAL ERROR` line is all that reaches stdout.
  - The protected result and `stdout.txt` are written for every run that got past stage 1 of the
    lock, and never under a hidden root that does not exist (§16.14).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from contextlib import AsyncExitStack, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn
from uuid import UUID

if __package__ in (None, ""):  # §3.1 script form: `python backend/tools/.../verify_step1.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if TYPE_CHECKING:  # pragma: no cover - typing only; every runtime import is inside the guard
    from tools.mem01_verify.run_identity import InputObserver
    from tools.mem01_verify.runner_output import RunState
    from tools.mem01_verify.runner_render import StdoutDecision, TeeStream

DEFAULT_RELEASE_NAME = "step1-gold-v1"
CLEANUP_FAILED_REASON = "cleanup_failed: {detail}"
ARTIFACTS_UNWRITABLE_REASON = "artifacts_unwritable: {detail}"
STDOUT_UNWRITABLE_REASON = "stdout unwritable: {violation}"
APP_LOG_UNWRITABLE_DETAIL = "app_log {violation}"  # §16.17(d)
INTERRUPTED_REASON = "interrupted: {violation}"  # §16.17(e)
USAGE_ERROR_LINE = "verify_step1: error: invalid usage; see --help"  # §16.17(f)
INTERNAL_ERROR_LINE = "MEM01 INTERNAL ERROR: {violation}"
#: §3.5 `statuses.EXIT_ERROR`, spelled out: the last-resort guard may import nothing at all.
INTERNAL_ERROR_EXIT_CODE = 2
CLEANUP_STEP = 11  # §3.2: the step a failed drop or a failed artifact write aborts at
PRINT_STEP = 12  # §3.2: the step the block is projected, validated and printed at
FIRST_SEQUENCE_STEP = 3  # §3.2: the step a run is at while the release is being verified


class ValueFreeArgumentParser(argparse.ArgumentParser):
    """§16.17(f): a parser whose usage errors name no value.

    Argparse's own `error` quotes the rejected argument — `--org person@example.test` would put
    an address on stderr — so this one prints a single fixed line instead and exits 2. Nothing
    else changes: `--help` still prints the help and exits 0 through `SystemExit`.
    """

    def error(self, message: str) -> NoReturn:
        """Print the one fixed usage line and exit 2; `message` is discarded (R5)."""
        sys.stderr.write(f"{USAGE_ERROR_LINE}\n")
        sys.stderr.flush()
        self.exit(2)


def build_parser() -> argparse.ArgumentParser:
    """Build the §3.1 option set; every long name is the contract's own."""
    parser = ValueFreeArgumentParser(
        prog="verify_step1",
        description="MEM-01 Phase 0 step-1 verifier (STAGE-A-CONTRACT §3).",
    )
    parser.add_argument("--release", default=None, help="the release directory")
    parser.add_argument("--expect-lock", default=None, help="sha256:<hex> the caller expects")
    parser.add_argument("--org", type=UUID, default=None, help="the corpus org for corpus gates")
    parser.add_argument("--checkpoint", action="store_true", help="score the hidden TEST split")
    parser.add_argument("--validation", action="store_true", help="the founder one-shot run")
    parser.add_argument("--gates", default=None, help="comma-separated gate subset (diagnostic)")
    parser.add_argument("--probe-db", default=None, help="reuse an existing probe database")
    parser.add_argument("--keep-probe", action="store_true", help="do not drop the probe")
    parser.add_argument("--report-dir", default=None, help="where per-gate reports go")
    parser.add_argument("--baseline-label", default=None, help="free label recorded in the block")
    return parser


def cli_options_of(options: argparse.Namespace) -> dict[str, object]:
    """The §3.11 `cli_options` of the closure — the RAW option values, in a stable order."""
    return {
        "release": options.release,
        "expect_lock": options.expect_lock,
        "org": str(options.org) if options.org else None,
        "checkpoint": options.checkpoint,
        "validation": options.validation,
        "gates": options.gates,
        "probe_db": options.probe_db,
        "keep_probe": options.keep_probe,
        "report_dir": options.report_dir,
        "baseline_label": options.baseline_label,
    }


def run_kind_of(options: argparse.Namespace) -> str:
    """`checkpoint`, `validation` or `tuning` — the run kind the options ask for (§3.3)."""
    if options.checkpoint:
        return "checkpoint"
    if options.validation:
        return "validation"
    return "tuning"


async def _sequence(
    state: RunState,
    options: argparse.Namespace,
    observer: InputObserver,
    hidden_root: Path,
) -> dict[str, object] | None:
    """Run steps 3 to 9; return a recorded free-repeat result when step 6 replayed one."""
    from tools.mem01_verify import db, runner_cleanup, runner_probe, runner_steps
    from tools.mem01_verify.runner_logging import open_app_log
    from tools.mem01_verify.runner_output import HIDDEN_RUN_KINDS

    state.step = FIRST_SEQUENCE_STEP
    selected = runner_steps.selected_gates(options.gates)
    release_dir = runner_probe.resolve_release_dir(options.release, DEFAULT_RELEASE_NAME)
    runner_steps.verify_release(state, release_dir=release_dir, expect_lock=options.expect_lock)
    runner_probe.assign_report_paths(
        state, report_dir_option=options.report_dir, hidden_root=hidden_root
    )
    org_id = options.org or runner_steps.manifest_org_id(state)
    state.org_id = org_id

    async with AsyncExitStack() as corpus_stack:
        state.step = 4
        # §16.14: no tree under an absent hidden root
        writable = runner_cleanup.artifacts_writable(state, hidden_root)
        open_app_log(state, state.require_report_dir() if writable else None)
        session = await corpus_stack.enter_async_context(db.readonly_corpus_snapshot(org_id))
        await runner_steps.identify_corpus(state, session, org_id)
        state.step = 5
        await runner_steps.read_database_identity(state, session)
        closure = runner_steps.build_identity(state, cli_options=cli_options_of(options))
        state.lease = await runner_probe.acquire_probe(
            state,
            selected=selected,
            probe_db_name=options.probe_db,
            keep=options.keep_probe,
            report_writable=writable,
        )
        if state.run_kind in HIDDEN_RUN_KINDS:
            state.step = 6
            sets = runner_steps.admit_hidden_run(state, hidden_root=hidden_root)
            recorded = getattr(state.reservation, "recorded_result", None)
            if recorded is not None:
                return dict(recorded)
            state.step = 7
            runner_steps.verify_hidden_split(state, hidden_root=hidden_root, sets=sets)
        state.step = 8
        await runner_steps.evaluate_gates(
            state,
            session=session,
            selected=selected,
            hidden_root=hidden_root if state.run_kind in HIDDEN_RUN_KINDS else None,
        )
        state.step = 9
        runner_steps.check_observer(state, observer, closure)
    return None


def _print_block(
    state: RunState, out: TeeStream, decision: StdoutDecision
) -> tuple[dict[str, object] | None, tuple[int, str] | None]:
    """Emit the already-decided machine block — no shape is chosen here (§16.16(d)/A21).

    Returns:
        The block that was printed and the abort the run now carries — `(None, abort)` when only
        the internal-error line was printed, so the caller prints no verdict and exits 2.
    """
    from tools.mem01_verify.runner_logging import write_guarded

    printed, abort, violation = decision.block, decision.abort, decision.violation
    if decision.text is not None:
        text = decision.text
        violation = write_guarded(lambda: out.line(text))
        if violation is None:
            return printed, abort
        abort = (state.step, STDOUT_UNWRITABLE_REASON.format(violation=violation))
    _emit_internal_error(violation, out)
    return None, abort


async def _run(options: argparse.Namespace, out: TeeStream) -> int:
    """Execute the §3.2 sequence, print the block (and the verdict), return the exit code."""
    from tools.mem01_verify import runner_cleanup
    from tools.mem01_verify.exceptions import HiddenBudgetExhaustedError
    from tools.mem01_verify.hashing import runner_sha256
    from tools.mem01_verify.run_identity import InputObserver, new_run_id
    from tools.mem01_verify.runner_logging import close_app_log
    from tools.mem01_verify.runner_output import RunState
    from tools.mem01_verify.runner_probe import REPO_ROOT, root_from_env

    started = datetime.now(UTC)
    state = RunState(
        run_kind=run_kind_of(options),
        run_id=new_run_id(started),
        started_at=started,
        partial=bool(options.gates),
        baseline_label=options.baseline_label,
    )
    hidden_root = root_from_env("hidden")
    recorded: dict[str, object] | None = None
    abort: tuple[int, str] | None = None
    exhaustion_line: str | None = None
    try:
        state.step = 2
        state.runner_digest = runner_sha256()  # §3.2 step 2 — inside the try (A25)
        with InputObserver(REPO_ROOT) as observer:  # §3.11: the literal step 2 → 9 window
            recorded = await _sequence(state, options, observer, hidden_root)
    except HiddenBudgetExhaustedError as error:  # §3.6: the refusal that prints its own line too
        abort, exhaustion_line = (state.step, str(error)), str(error)
    except (KeyboardInterrupt, asyncio.CancelledError) as error:  # §16.17(e): an ABORTED run
        abort = (state.step, INTERRUPTED_REASON.format(violation=type(error).__name__))
    except Exception as error:  # noqa: BLE001 - any crash is an aborted run, never a traceback
        abort = (state.step, runner_cleanup.refusal_reason(error))
    try:
        if recorded is not None:
            return await _finish_free_repeat(state, out, hidden_root, recorded)
        return await _finish(state, options, out, hidden_root, abort, exhaustion_line)
    finally:
        try:
            # A27: isolated, so a failed close never skips the drop; §16.17(d) makes the close
            # itself total, and the class it returns was folded at step 11 — ignore it here
            close_app_log(state)
        finally:
            await runner_cleanup.drop_probe(state, keep=state.probe_kept)  # §16.16(j)


async def _finish_free_repeat(
    state: RunState, out: TeeStream, hidden_root: Path, recorded: dict[str, object]
) -> int:
    """§3.2 step 6: replay a completed pair's recorded result and stop (nothing was charged).

    The full path's rules hold here too: the printed shape is decided before the write
    (§16.16(t)/A21 — this path records no outcome), and the transcript goes through
    `artifacts_writable` (A23). The replay prints the recorded block alone — no line follows it.
    §16.17(i): a probe this path cannot drop is an ABORTED run — the aborted block replaces the
    recorded one and the exit is 2, while the pair's completed outcome stands untouched.
    §16.17(d): the app-log failures are folded under the same rule and BEFORE the replay, so an
    `OSError`-family emit or close failure refuses it too (a failed drop still wins).
    """
    from tools.mem01_verify import runner_cleanup, runner_logging
    from tools.mem01_verify.runner_render import decide_stdout
    from tools.mem01_verify.statuses import EXIT_ERROR, exit_code_for

    cleanup_failure = await runner_cleanup.drop_probe(state, keep=state.probe_kept)
    # §16.17(d): the capture's failures are read, and its close classified, BEFORE the replay
    state.app_log_emit_failures = runner_logging.read_and_close_app_log(state)
    abort = _fold_cleanup_abort(state, None, cleanup_failure)
    block: dict[str, object] = dict(recorded)
    if abort is not None:  # §16.17(d)/(i): the replay is refused, nothing is recorded
        state.step = CLEANUP_STEP  # the step the fallback block would report too
        block = runner_cleanup.assemble_block(state, abort)
    else:
        state.step = PRINT_STEP  # A27: a refused replay aborts at the step it was printing from
    decision = decide_stdout(
        state, block, abort, duration_ms=runner_cleanup.elapsed_ms(state), emit_lines=False
    )
    _, abort = _print_block(state, out, decision)
    if runner_cleanup.artifacts_writable(state, hidden_root):
        runner_cleanup.finalize_report(state, out.transcript, aborted=abort is not None)
    if abort is not None:
        return EXIT_ERROR
    return exit_code_for(str(recorded.get("status")))


def _fold_cleanup_abort(
    state: RunState, abort: tuple[int, str] | None, cleanup_failure: str | None
) -> tuple[int, str] | None:
    """Fold the step-11 infrastructure failures into `abort`, in the contract's own precedence.

    §16.16(t) fixes the head of the order — a failed drop wins over everything — and §16.17(d)
    adds the app-log failures at the tail: an `OSError`-family class, whether it was recorded
    while EMITTING or classified while CLOSING the capture, is an artifact-write failure like any
    other, and any other class is evidence only and the run completes. Both the full path and the
    §3.2 step-6 free repeat fold through here, so the precedence is decided in one place.
    """
    if abort is not None:
        return abort
    if cleanup_failure is not None:
        return (CLEANUP_STEP, CLEANUP_FAILED_REASON.format(detail=cleanup_failure))
    if state.artifact_write_failure is not None:
        detail = state.artifact_write_failure
        return (CLEANUP_STEP, ARTIFACTS_UNWRITABLE_REASON.format(detail=detail))
    from tools.mem01_verify.runner_logging import first_os_error_emit_failure

    emit_failure = first_os_error_emit_failure(state.app_log_emit_failures)
    if emit_failure is None:
        return None
    detail = APP_LOG_UNWRITABLE_DETAIL.format(violation=emit_failure)
    return (CLEANUP_STEP, ARTIFACTS_UNWRITABLE_REASON.format(detail=detail))


async def _finish(
    state: RunState,
    options: argparse.Namespace,
    out: TeeStream,
    hidden_root: Path,
    abort: tuple[int, str] | None,
    exhaustion_line: str | None,
) -> int:
    """Steps 10 to 13: the protected result, the cleanup, the printing and the exit code.

    Order matters at step 12: the printed shape is decided (projected, validated and rendered)
    BEFORE the reservation and the journal see this run's outcome, so a refused projection — or
    a render that raises — is an ABORTED run reserving no verdict (A21/A25). The §16.16(n)
    rewrite comes EARLIER still, on the post-cleanup block, so a rewrite the filesystem refuses
    becomes the run's abort rather than a silently discarded failure (§16.17(g)); the rewrite
    that follows a refused projection stays best-effort, that path being aborted already. B12: a
    rewrite failure is retained in `diagnostics.run.artifact_write_failure` even when it loses the
    precedence — a `cleanup_failed` run still reports which class refused the rewrite.
    """
    from tools.mem01_verify import runner_cleanup, runner_logging, runner_steps
    from tools.mem01_verify.runner_render import decide_stdout
    from tools.mem01_verify.statuses import EXIT_ERROR, exit_code_for

    writable = runner_cleanup.artifacts_writable(state, hidden_root)
    state.step = 10
    block = runner_cleanup.assemble_block(state, abort)
    if writable:
        state.artifact_write_failure = runner_logging.write_run_artifacts(state, block)
    state.step = CLEANUP_STEP
    cleanup_failure = await runner_cleanup.drop_probe(state, keep=options.keep_probe)
    # §16.17(d): the capture's own failures are read while it is still installed, never after;
    # the close is classified the same way and merged into the one list the fold reads
    state.app_log_emit_failures = runner_logging.read_and_close_app_log(state)
    abort = _fold_cleanup_abort(state, abort, cleanup_failure)
    block = runner_cleanup.assemble_block(state, abort)
    if writable:  # §16.17(g): the rewrite is checked while the outcome is still undecided
        rewrite_failure = runner_cleanup.rewrite_protected_result(state, block)
        if rewrite_failure is not None:
            if state.artifact_write_failure is None:  # B12: kept even when `cleanup_failed` won
                state.artifact_write_failure = rewrite_failure
            if abort is None:
                abort = (CLEANUP_STEP, ARTIFACTS_UNWRITABLE_REASON.format(detail=rewrite_failure))
            block = runner_cleanup.assemble_block(state, abort)
    state.step = PRINT_STEP
    decision = decide_stdout(  # §16.16(t)/A21: decide every line, then record
        state, block, abort, duration_ms=runner_cleanup.elapsed_ms(state)
    )
    abort = decision.abort
    if decision.refused_block:
        # the artifact and the journal record the real outcome
        block = runner_cleanup.assemble_block(state, abort)
    runner_cleanup.record_hidden_outcome(state, block, aborted=abort is not None)
    if writable and decision.refused_block:
        runner_cleanup.rewrite_protected_result(state, block)  # best-effort: already aborted
    printed, abort = _print_block(state, out, decision)
    if printed is not None and abort is None:
        for line in decision.set_lines:  # §3.8: one per SET scored on the hidden split
            out.line(line)
        if decision.verdict is not None:
            out.line(decision.verdict)
            runner_steps.record_verdict_printed(state, decision.verdict)
    if exhaustion_line is not None and printed is not None:
        out.line(exhaustion_line)
    if writable:
        runner_cleanup.finalize_report(state, out.transcript, aborted=abort is not None)
    return EXIT_ERROR if abort is not None else exit_code_for(str(block["status"]))


def _force_utf8_streams() -> None:
    """R8/A25: put stdout and stderr in UTF-8 before anything at all can write to either.

    It runs FIRST and unconditionally, so even a failure raised while the runner is being
    imported is reported in UTF-8 and not in the platform's code page (the §3.9 self-test then
    proves the stream carries Cyrillic). A stream that cannot be reconfigured is left as it is.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with suppress(Exception):  # R11: a stream that refuses is not a reason to die
            reconfigure(encoding="utf-8")


def _emit_internal_error(violation: str | None, out: TeeStream | None = None) -> None:
    """Print the single §16.16(t) internal-error line; its OWN failure is suppressed (R11).

    `out` is the tee when the run got far enough to have one, so the line joins the transcript;
    the last-resort guard has none and writes straight to the process's stdout.
    """
    with suppress(Exception):
        line = INTERNAL_ERROR_LINE.format(violation=violation)
        if out is None:
            sys.stdout.write(f"{line}\n")
            sys.stdout.flush()
        else:
            out.line(line)


def _guarded_main(argv: list[str] | None) -> int:
    """Everything the guard covers: the self-test, the options and the run itself.

    The imports live HERE, not in the module body, so a configuration error raised while
    `app.core.database` is imported reaches the guard instead of escaping as a traceback (A25);
    they all happen before the observer window opens, so the steps' own imports resolve from
    `sys.modules` and open no file inside the window (§3.11).
    """
    from tools.mem01_verify.exceptions import Utf8SelfTestError
    from tools.mem01_verify.runner_render import TeeStream
    from tools.mem01_verify.statuses import EXIT_ERROR
    from tools.mem01_verify.utf8_selftest import UTF8_SELFTEST_FAILED_LINE, run_utf8_selftest

    out = TeeStream(sys.stdout)
    try:
        run_utf8_selftest(out, Path(tempfile.gettempdir()))
    except Utf8SelfTestError:
        out.line(UTF8_SELFTEST_FAILED_LINE)
        return EXIT_ERROR
    options = build_parser().parse_args(argv)
    from app.core.config import get_settings

    get_settings()  # warm the process-wide settings cache before the observer window opens
    return asyncio.run(_run(options, out))


def main(argv: list[str] | None = None) -> int:
    """Run the §3.2 sequence for `argv` (defaults to `sys.argv[1:]`) and return the exit code.

    This is the ONE R11 guard (§16.16(t)/A25): both streams are forced to UTF-8, then everything
    else happens inside a single `try`. Any exception becomes one `MEM01 INTERNAL ERROR: <class>`
    line and exit 2, with no traceback on stderr. A `KeyboardInterrupt` or an
    `asyncio.CancelledError` raised OUTSIDE the §3.2 sequence — a programmatic `Task.cancel()`,
    or a second interruption landing during steps 10-13 — is reported the same way, by its own
    class (§16.17(e); one raised inside the sequence is the aborted run of `_run`); only
    `SystemExit` passes through, so argparse's `--help` still exits 0 and its value-free usage
    error exits 2.
    """
    _force_utf8_streams()
    try:
        return _guarded_main(argv)
    except (KeyboardInterrupt, asyncio.CancelledError) as error:  # §16.17(e): never a traceback
        _emit_internal_error(type(error).__name__)
        return INTERNAL_ERROR_EXIT_CODE
    except Exception as error:  # noqa: BLE001 - R11: a run never dies with a traceback
        _emit_internal_error(type(error).__name__)
        return INTERNAL_ERROR_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main())
