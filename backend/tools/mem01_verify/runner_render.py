"""
Role: The strings a run PRINTS — the tee that keeps the transcript of stdout, the two §3.8 line
      forms, and the §16.16(t)/A21 stdout decision: the §3.4 projection, its validation and every
      rendered line, all settled BEFORE the run's outcome is recorded anywhere.
Used by: `tools.mem01_verify.verify_step1` (the only module that prints).
Depends on: `tools.mem01_verify.result_block` (project / validate / render), `.runner_output`
      (`HIDDEN_RUN_KINDS` and the §16.16(t) minimal aborted fallback), `.verdict`, `.statuses`;
      `RunState` under `TYPE_CHECKING` only, so this module never imports the database layer.
Key invariants:
  - `decide_stdout` is pure with respect to the run's durable state: it prints no line, writes no
    artifact and records no outcome, so the caller settles the printed shape BEFORE the hidden
    ledger or the validation journal sees this run (§16.16(t)/A21). A refused projection folds
    into `abort` and the minimal aborted block of §16.16(t) replaces it; a render that raises
    leaves `text is None`, and the caller's single `MEM01 INTERNAL ERROR` line is then all that
    reaches stdout.
  - A refused projection and a failed render are reported as the failing exception's CLASS name,
    never as its message (R5: a message can quote a field or a value; R11: never a traceback).
  - Nothing rendered here carries personal data (R5): the verdict fields are hashes, ids and gate
    names, and a per-SET line carries a SET name and a status.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, TextIO

from tools.mem01_verify.result_block import (
    project_for_stdout,
    render_result_block,
    validate_result_block,
)
from tools.mem01_verify.runner_output import HIDDEN_RUN_KINDS, build_minimal_aborted_block
from tools.mem01_verify.statuses import PASS
from tools.mem01_verify.verdict import VerdictFields, format_verdict_line

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tools.mem01_verify.runner_output import RunState

PROJECTION_REASON = "internal projection error: {violation}"
RENDER_REASON = "internal render error: {violation}"
_COLLAPSED_ENTRY_KEYS = frozenset({"id", "split", "status"})


class TeeStream:
    """`sys.stdout` with a transcript, so `stdout.txt` can be exactly what was printed.

    It carries the three members the runner and the §3.9 self-test use — `reconfigure`, `write`
    and `flush` — and forwards each to the wrapped stream, keeping every written chunk.
    """

    def __init__(self, stream: TextIO) -> None:
        """Wrap `stream` (the process's real stdout)."""
        self._stream = stream
        self._chunks: list[str] = []

    def reconfigure(self, **options: object) -> None:
        """Forward a `reconfigure` call (the self-test forces UTF-8 through it)."""
        reconfigure = getattr(self._stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(**options)

    def write(self, text: str) -> int:
        """Record `text` in the transcript and write it to the wrapped stream."""
        self._chunks.append(text)
        return self._stream.write(text)

    def flush(self) -> None:
        """Flush the wrapped stream."""
        self._stream.flush()

    def line(self, text: str) -> None:
        """Write one line and flush, so ordering on the pipe matches the run sequence."""
        self.write(text + "\n")
        self.flush()

    @property
    def transcript(self) -> str:
        """Everything written through this stream so far."""
        return "".join(self._chunks)


def hidden_set_lines(block: Mapping[str, object]) -> tuple[str, ...]:
    """§3.8: one `<SET>: GATES PASS|FAIL` line per SET actually scored on the hidden split.

    Args:
        block: The STDOUT PROJECTION of a hidden run — only there are the criteria entries of
            the evaluated split collapsed to `{id, split, status}`, which is what a SET line
            reports.

    Returns:
        The lines in the projection's own order; empty when no SET was scored.
    """
    entries = block.get("criteria")
    split = block.get("split_evaluated")
    if not isinstance(entries, list):
        return ()
    return tuple(
        f"{entry['id']}: GATES {entry['status']}"
        for entry in entries
        if isinstance(entry, Mapping)
        and entry.get("split") == split
        and frozenset(entry) == _COLLAPSED_ENTRY_KEYS
    )


def verdict_line(state: RunState, block: Mapping[str, object]) -> str:
    """§3.8: render the single verdict line of a completed run.

    `block` is the completed protected result — `<k>/17` counts its `PASS` gates, and the lock,
    runner and provisional fields are read from it rather than recomputed.
    """
    gates = block["gates"]
    passed = sum(1 for entry in gates.values() if entry["status"] == PASS)  # type: ignore[union-attr]
    return format_verdict_line(
        VerdictFields(
            run_kind=state.run_kind,  # type: ignore[arg-type]  # one of the three run kinds
            passed=passed,
            provisional=tuple(block["provisional_gates"]),  # type: ignore[arg-type]
            directional=tuple(block["directional_gates"]),  # type: ignore[arg-type]
            run_id=state.run_id,
            lock_sha256=str(block["release_lock_sha256"]),
            runner_sha256=str(block["runner_sha256"]),
            hidden=state.hidden,
            validation_complete=state.validation_complete,
        )
    )


@dataclass(frozen=True)
class StdoutDecision:
    """What stdout will carry, settled before any outcome is recorded (§16.16(t)/A21).

    Attributes:
        block: The projection the §3.4 stdout schema accepted — the run's own block or the
            minimal aborted fallback — and None when even the fallback was refused.
        text: The rendered machine block, or None when nothing but the internal-error line may
            be printed (a refused fallback, or a render that raised).
        set_lines: The §3.8 per-SET lines of a completed hidden run; empty otherwise.
        verdict: The §3.8 verdict line of a completed run; None when the run aborted.
        violation: The CLASS name behind `text is None` (R5: a class, never a message).
        abort: The abort the run carries after the decision — the one it came in with, or the
            projection / render failure that supersedes it.
        refused_block: True when the schema refused the block this run assembled or its render
            failed, so the caller records and writes the ABORTED shape instead.
    """

    block: dict[str, object] | None
    text: str | None
    set_lines: tuple[str, ...]
    verdict: str | None
    violation: str | None
    abort: tuple[int, str] | None
    refused_block: bool


def _project(block: Mapping[str, object]) -> tuple[dict[str, object] | None, str | None]:
    """Project `block` for stdout and validate it; return the projection or the violation CLASS.

    Both halves are guarded together: a projection that RAISES is the same internal failure as
    one the §3.4 schema refuses (R11), and only the CLASS is ever reported, never a message (R5).
    """
    try:
        projected = project_for_stdout(dict(block))
        validate_result_block(projected, projection="stdout")
    except Exception as error:  # noqa: BLE001 - R5/R11: a refused projection names its class
        return None, type(error).__name__
    return projected, None


def _render(
    state: RunState,
    projected: Mapping[str, object],
    block: Mapping[str, object],
    *,
    lines: bool,
) -> tuple[str | None, tuple[str, ...], str | None, str | None]:
    """Render the block text and, for a completed run, the §3.8 lines; or name the failure CLASS.

    The render is guarded like an artifact write (§16.16(t)/A22): a `render_result_block` or a
    verdict render that raises never leaves the process as a traceback — the caller prints the
    single `MEM01 INTERNAL ERROR` line instead and the run aborts.
    """
    try:
        text = render_result_block(dict(projected))
        if not lines:
            return text, (), None, None
        sets = hidden_set_lines(projected) if state.run_kind in HIDDEN_RUN_KINDS else ()
        return text, sets, verdict_line(state, block), None
    except Exception as error:  # noqa: BLE001 - R5/R11: a failed render names its class
        return None, (), None, type(error).__name__


def decide_stdout(
    state: RunState,
    block: Mapping[str, object],
    abort: tuple[int, str] | None,
    *,
    duration_ms: int,
    emit_lines: bool = True,
) -> StdoutDecision:
    """Decide every line stdout will carry — it prints nothing, writes nothing, records nothing.

    §16.16(t)/A21: this runs BEFORE the hidden ledger or the validation journal record anything,
    so a refused projection or a failed render folds into `abort` first and the run is recorded
    as ABORTED rather than completed or verdict-reserved. The replacement shape is the MINIMAL
    aborted block of §16.16(t), whose reason names only the violation CLASS (never the fields,
    which could quote evidence).

    Args:
        state: The run so far — its step is the step an internal failure is reported at.
        block: The block this run assembled (or, on the §3.2 step-6 free repeat, the recorded
            protected result it replays).
        abort: The abort the run already carries, or None while it is still a completed run.
        duration_ms: The run's wall-clock length, for the fallback block's envelope.
        emit_lines: False on the free-repeat replay, which prints the recorded block alone.

    Returns:
        The decision the caller prints, records and writes against.
    """
    projected, violation = _project(block)
    refused = violation is not None
    if projected is None:
        abort = (state.step, PROJECTION_REASON.format(violation=violation))
        fallback = build_minimal_aborted_block(
            state, step=state.step, reason=abort[1], duration_ms=duration_ms
        )
        projected, violation = _project(fallback)
        if projected is None:
            return StdoutDecision(None, None, (), None, violation, abort, True)
    text, set_lines, verdict, failure = _render(
        state, projected, block, lines=emit_lines and abort is None
    )
    if failure is not None:
        abort = (state.step, RENDER_REASON.format(violation=failure))
        return StdoutDecision(projected, None, (), None, failure, abort, True)
    return StdoutDecision(projected, text, set_lines, verdict, None, abort, refused)
