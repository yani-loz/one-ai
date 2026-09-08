"""
Role: The run's REPORT DIRECTORY and the app-logger capture — where the artifacts of §3.3/§16.11
      go and how they get there: the §16.16(k) capture with its §16.16(t) discard sink, the
      guarded write that never turns ANY exception into a traceback, the three artifacts
      themselves, the §16.16(i) report-root-relative `protected_result_path` that ledger and
      journal events record, and the §16.16(h) sealing of an aborted validation attempt.
Used by: `tools.mem01_verify.runner_cleanup` and `.verify_step1`; `.runner_output` re-exports
      `capture_app_logging` and `protected_result_relpath` under their contract names. Sealed by
      `tests/tools/mem01_verify/test_app_logging_capture.py`,
      `test_write_guarded_degrades_any_exception.py`, `test_runner_output_relpath.py` and,
      through the CLI, by `test_verify_step1_*.py`.
Depends on: `tools.mem01_verify.app_log_names` (the `app` logger name and the log format, the
      leaf the RED gate shares — B11), `.gates.context` (the per-gate report writer),
      `.exceptions`, and `.validation_guard` lazily (imported inside
      `seal_aborted_validation_attempt`); `RunState` and `GateResult` under `TYPE_CHECKING`
      only, so this module stays a leaf at import time —
      the import graph runner_logging → runner_output → {runner_render, runner_probe} →
      runner_steps → runner_cleanup → verify_step1 has no cycle.
Key invariants:
  - Inside `capture_app_logging` NO record of `app` or of any descendant reaches a stream: the
    descendants' handlers are detached and their `propagate` forced on, so every record funnels
    through the single handler on `app`; every logger touched is restored on exit, through
    exceptions included. `discard_app_logging` is that same capture with an in-memory sink, so a
    run that may write no report directory still holds the window open (§16.16(t)).
  - No capture handler ever reports an emit failure to a stream (§16.17(d)): both the file
    handler and the discard sink override `handleError` to append the failing exception's most
    specific BUILTIN class name to their own `emit_failures` list, and `app_log_emit_failures`
    reports the sorted distinct names of the handlers currently installed on `app` — so the
    reading resets when the capture closes and the module-global `logging.raiseExceptions` is
    never touched. Closing the capture is held to the same rule: `close_app_log` RETURNS the
    failing class instead of raising, and `read_and_close_app_log` merges that class into the
    emit failures so the runner folds one sorted distinct list at step 11.
  - No guarded write escapes as a traceback: `write_guarded` turns ANY exception — a `TypeError`
    from a block json cannot serialise exactly like an `OSError` from a denied path — into the
    CLASS name the caller reports as a detail (R5: a message quotes a path or a value; §16.16(t)
    and R11: a run never dies with a traceback).
  - Nothing here writes to stdout or stderr: the lines a run prints are rendered in
    `runner_render` and printed by `verify_step1` alone.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING

from tools.mem01_verify.app_log_names import APP_LOG_FORMAT, APP_LOGGER_NAME
from tools.mem01_verify.exceptions import IntegrityViolationError, Mem01Error
from tools.mem01_verify.gates.context import write_gate_report

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tools.mem01_verify.gates.context import GateResult
    from tools.mem01_verify.runner_output import RunState

APP_LOG_FILENAME = "app.log"
PROTECTED_RESULT_NAME = "protected_result.json"
STDOUT_TRANSCRIPT_NAME = "stdout.txt"
#: How many records the discard sink keeps before dropping the oldest — bounded, never read.
_DISCARD_SINK_CAPACITY = 1000


def _builtin_os_error_class_names() -> frozenset[str]:
    """The names of `OSError` and of every BUILTIN class derived from it (§16.17(d))."""
    names = {OSError.__name__}
    pending: list[type[BaseException]] = [OSError]
    while pending:
        for subclass in pending.pop().__subclasses__():
            if subclass.__module__ == "builtins" and subclass.__name__ not in names:
                names.add(subclass.__name__)
                pending.append(subclass)
    return frozenset(names)


#: §16.17(d): the builtin `OSError` family, by NAME, computed once — a recorded emit failure
#: whose class name is in here is an artifact-write failure, any other is evidence only.
OS_ERROR_CLASS_NAMES: frozenset[str] = _builtin_os_error_class_names()


def _builtin_class_name(error: BaseException) -> str:
    """The most specific BUILTIN class in `error`'s MRO (§16.17(d)), by name.

    A third-party subclass of a builtin — a vendor `PermissionError`, say — is folded onto the
    builtin it derives from, so the recorded vocabulary is closed and R5-safe. An exception with
    no builtin ancestor at all (impossible for a real one) keeps its own class name.
    """
    for ancestor in type(error).__mro__:
        if ancestor.__module__ == "builtins":
            return ancestor.__name__
    return type(error).__name__


def first_os_error_emit_failure(names: Sequence[str]) -> str | None:
    """The first recorded emit-failure class name in the builtin `OSError` family, or None."""
    return next((name for name in names if name in OS_ERROR_CLASS_NAMES), None)


class EmitFailureRecordingHandler(logging.Handler):
    """A handler whose emit failures are RECORDED, never reported to a stream (§16.17(d)).

    `logging.Handler.handleError` prints the record and its traceback to stderr whenever
    `logging.raiseExceptions` is on; under the runner's capture that would put an application
    record — and therefore possibly personal data — on a stream R5 forbids it to reach. This
    override keeps the failing exception's builtin class name instead, and the runner reads the
    collected names at step 11.
    """

    emit_failures: list[str]

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802 - stdlib hook name
        """Record the in-flight exception's builtin class name; touch no stream, raise nothing."""
        error = sys.exc_info()[1]
        if error is not None:
            self.emit_failures.append(_builtin_class_name(error))


class AppLogFileHandler(EmitFailureRecordingHandler, logging.FileHandler):
    """The `app.log` handler of §16.16(k): a file handler that records its emit failures.

    It is the ONLY handler `capture_app_logging` installs on the `app` logger, so a record the
    file cannot take — a full disk, a denied path, a message whose arguments do not format —
    leaves a class name behind instead of a record and a traceback on stderr (§16.17(d), R5).
    """

    def __init__(self, filename: Path, *, encoding: str) -> None:
        """Open `filename` for append in `encoding`, with an empty emit-failure list."""
        self.emit_failures = []
        super().__init__(filename, encoding=encoding)


def app_log_emit_failures() -> tuple[str, ...]:
    """The distinct emit-failure classes of the handlers currently installed on `app` (§16.17(d)).

    Returns:
        The sorted distinct CLASS names recorded so far — an empty tuple outside a capture, and
        again once one has closed, because the capture restores the handlers it displaced.
    """
    names: set[str] = set()
    for handler in logging.getLogger(APP_LOGGER_NAME).handlers:
        names.update(getattr(handler, "emit_failures", ()))
    return tuple(sorted(names))


def _app_descendants() -> list[logging.Logger]:
    """Every logger already registered under `app.` — those whose handlers must be detached.

    `logging.getLogger` materializes a placeholder into a real logger; that is what lets the
    capture own the descendant's `handlers` and `propagate` for its lifetime.
    """
    prefix = f"{APP_LOGGER_NAME}."
    names = sorted(logging.Logger.manager.loggerDict)
    return [logging.getLogger(name) for name in names if name.startswith(prefix)]


class DiscardingLogSink(EmitFailureRecordingHandler):
    """The in-memory destination of §16.16(t) — records are kept, bounded, and never read.

    A run that may write no report directory still owes R5: its application records must not
    reach stderr. They come here instead of to a stream, and the sink dies with the run; an emit
    failure is recorded by class the way the file handler records one (§16.17(d)).
    """

    def __init__(self) -> None:
        """Accept every level; keep at most `_DISCARD_SINK_CAPACITY` records."""
        super().__init__(level=logging.DEBUG)
        self.emit_failures = []
        self._records: deque[logging.LogRecord] = deque(maxlen=_DISCARD_SINK_CAPACITY)

    def emit(self, record: logging.LogRecord) -> None:
        """Keep `record` in memory; it reaches no stream and no file."""
        self._records.append(record)


@contextmanager
def _app_hierarchy_through(handler: logging.Handler) -> Iterator[None]:
    """Funnel every `app` record through `handler`, restoring each logger touched on exit."""
    app = logging.getLogger(APP_LOGGER_NAME)
    touched = [app, *_app_descendants()]
    saved = [(logger, list(logger.handlers), logger.propagate) for logger in touched]
    saved_level = app.level
    try:
        for logger in touched:
            logger.handlers = []
            logger.propagate = logger is not app
        app.handlers = [handler]
        app.setLevel(logging.DEBUG)
        yield
    finally:
        for logger, handlers, propagate in saved:
            logger.handlers = handlers
            logger.propagate = propagate
        app.setLevel(saved_level)
        handler.close()


@contextmanager
def capture_app_logging(report_dir: Path) -> Iterator[Path]:
    """Route every `app` record to `<report_dir>/app.log` and to no stream (§16.16(k), R5).

    The runner holds this open from step 4 to step 11: an application WARNING raised while a gate
    reads the corpus is evidence for the report, never a line on stdout or stderr, because R5
    forbids personal data on either stream and an application record may carry some.

    Args:
        report_dir: The run's report directory; created when it does not exist yet.

    Yields:
        The path of the log file the capture writes to.
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / APP_LOG_FILENAME
    handler = AppLogFileHandler(path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(APP_LOG_FORMAT))
    with _app_hierarchy_through(handler):
        yield path


@contextmanager
def discard_app_logging() -> Iterator[None]:
    """The §16.16(t) capture for a run that may write no report directory: an in-memory sink."""
    with _app_hierarchy_through(DiscardingLogSink()):
        yield


def open_app_log(state: RunState, report_dir: Path | None) -> None:
    """Open the step-4 capture and park it on `state` until step 11 closes it (§16.16(k)/(t)).

    `report_dir` is None when this run may write no report directory (§16.14 forbids a tree
    under an absent hidden root): the records then go to the discard sink, never to a stream.
    """
    stack = ExitStack()
    stack.enter_context(
        discard_app_logging() if report_dir is None else capture_app_logging(report_dir)
    )
    state.log_capture = stack


def close_app_log(state: RunState) -> str | None:
    """Restore the logging configuration once step 11 is done; idempotent, and never raises.

    §16.17(d): a capture whose `flush()` or `close()` fails — the full disk under `app.log` the
    determination names — must not turn the run into a traceback. The failure is classified the
    way an emit failure is and RETURNED, so the caller folds it at step 11 alongside the classes
    `app_log_emit_failures` reported. A `BaseException` that is not an `Exception` still
    propagates: a cancellation landing on the close is §16.17(e)'s to report, not this one's.

    Args:
        state: The run holding the open capture on `state.log_capture`.

    Returns:
        The most specific BUILTIN class name of a failing close — R5-safe, unlike its message —
        or None when the close was clean and on every later call: the capture is detached from
        `state` BEFORE it is closed, so a repeat closes nothing and reports nothing.
    """
    stack = state.log_capture
    if stack is None:
        return None
    state.log_capture = None
    try:
        stack.close()  # type: ignore[attr-defined]  # contextlib.ExitStack
    except Exception as error:  # noqa: BLE001 - §16.17(d): a failed close names its class
        return _builtin_class_name(error)
    return None


def read_and_close_app_log(state: RunState) -> tuple[str, ...]:
    """Read the capture's emit failures, close it, and fold the close's own failure in (§16.17(d)).

    The reading happens while the capture is still installed — `app_log_emit_failures` reports
    the handlers currently on `app`, and closing restores the ones the capture displaced — and a
    close that fails is classified exactly like an emit failure, so the runner folds ONE list at
    step 11 on the full path and on the free repeat alike.

    Args:
        state: The run holding the open capture on `state.log_capture`.

    Returns:
        The sorted distinct CLASS names of every emit failure the capture recorded plus, when the
        close itself failed, its own class; empty when nothing failed and outside a capture.
    """
    names = set(app_log_emit_failures())
    close_failure = close_app_log(state)
    if close_failure is not None:
        names.add(close_failure)
    return tuple(sorted(names))


def write_guarded(*writes: Callable[[], object]) -> str | None:
    """Perform the writes in order, stopping at the first failure of ANY class (§16.16(t)).

    Every caller of this helper owes R11: an artifact write — and the render of the line the
    runner prints — must never leave the process as a traceback, whatever it raises. `OSError`
    is only the expected class; a `TypeError` from a block json cannot serialise degrades the
    same way.

    Args:
        writes: The zero or more callables to perform, in order; nothing to write succeeds.

    Returns:
        The failing exception's CLASS name — R5-safe, unlike its message, which quotes a path or
        a value — or None when every write succeeded. The caller turns it into the
        `artifacts_unwritable` abort reason, never into `cleanup_failed`.
    """
    for write in writes:
        try:
            write()
        except Exception as error:  # noqa: BLE001 - R5/R11: a write never becomes a traceback
            return type(error).__name__
    return None


def protected_result_relpath(report_dir: Path, report_root: Path) -> str:
    """The protected result's path relative to its report root, posix-separated (§16.16(i)).

    Args:
        report_dir: This run's report directory, named after its run id.
        report_root: The hidden root on hidden runs; the release directory — or the
            `--report-dir` directory when that option was given — on tuning runs.

    Returns:
        `<report dir relative to the root>/protected_result.json`: never absolute, never with a
        backslash. This is the value the ledger and the journal record, so a reader can find the
        artifact under whichever root the run wrote to.

    Raises:
        IntegrityViolationError: `report_dir` does not lie inside `report_root` — a sibling whose
            name merely shares the root's prefix included.
    """
    root = Path(os.path.abspath(report_root))
    directory = Path(os.path.abspath(report_dir))
    try:
        relative = directory.relative_to(root)
    except ValueError as error:
        raise IntegrityViolationError(
            "the report directory lies outside the report root it would be recorded against"
        ) from error
    return (relative / PROTECTED_RESULT_NAME).as_posix()


def write_protected_result(report_dir: Path, block: Mapping[str, object]) -> Path:
    """Write `<report dir>/protected_result.json` (the full block) and return its path."""
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / PROTECTED_RESULT_NAME
    path.write_text(
        json.dumps(block, ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8"
    )
    return path


def write_stdout_transcript(report_dir: Path, transcript: str) -> Path:
    """Write `<report dir>/stdout.txt` with exactly what was printed (§16.11)."""
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / STDOUT_TRANSCRIPT_NAME
    path.write_text(transcript, encoding="utf-8", newline="")
    return path


def write_absent_gate_reports(report_dir: Path, results: Mapping[str, GateResult]) -> None:
    """Write `gates/<GATE>.json` for every result whose evaluator wrote none (§16.13).

    A gate `--gates` left out is `skipped`, so the registry returns a result with no report
    file; the report directory still owes one file per gate.
    """
    for name, result in results.items():
        if result.report_files:
            continue
        write_gate_report(
            report_dir,
            name=name,
            status=result.status,
            reason=result.reason,
            criteria=result.criteria,
            cases=(),
            diagnostics=result.diagnostics,
        )


def write_run_artifacts(state: RunState, block: Mapping[str, object]) -> str | None:
    """Step 10: the protected result and the gate reports no evaluator wrote, guarded."""
    report_dir = state.require_report_dir()
    return write_guarded(
        lambda: write_protected_result(report_dir, block),
        lambda: write_absent_gate_reports(report_dir, state.gate_results),
    )


def seal_aborted_validation_attempt(state: RunState, *, aborted: bool) -> None:
    """§3.7/§16.16(h): an ADMITTED `--validation` attempt that aborted seals its report directory.

    Called last, once every artifact of the attempt is on disk. R11 forbids any further line, so
    a seal the guard refuses (an earlier `<run_id>.sealed` is already there, or the rename is
    denied) leaves both directories exactly as they were and shows only in the report tree.
    """
    if not aborted or state.run_kind != "validation" or state.attempt_id is None:
        return
    from tools.mem01_verify import validation_guard

    with suppress(Mem01Error, OSError):
        validation_guard.seal_aborted_attempt(state.require_report_dir())
