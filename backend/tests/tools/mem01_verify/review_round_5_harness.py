"""
Role: Shared apparatus of the round-5 seals that drive the runner's step 10-13 half
      (`verify_step1._finish`) IN PROCESS — a COMPLETE `RunState` whose completed block passes
      the stdout schema of its run kind (tuning, checkpoint or validation shaped), the
      string-backed tee that drives `_finish`, the `app`-logger stream breaker that injects an
      emit failure into a REAL capture, and the two-name patch of `write_protected_result`.
Used by: test_review_round_5_logging.py, test_review_round_5_logging_b.py,
      test_review_round_5_runner_b.py, test_review_round_5_runner_c.py.
Depends on: tests.tools.mem01_verify.reference, .conftest (GATE_NAMES, InstrumentLoader),
      .result_block_samples (VERSION_KEYS); the instrument only through the `instrument` loader
      the caller passes in (never imported at module level).
Key invariants:
  - `completed_run_state` builds a run whose COMPLETED block validates under the stdout schema
    of its run kind (nine hex64 hash fields, the 13 version keys, a corpus identity, 17
    `incomplete` gate results, `validation_complete` on a validation run), so a completed run
    is the baseline of every drive and any abort observed is the one the fault under test caused.
  - Nothing here prints or opens a database: the tee wraps a StringIO and the report tree is
    written under the caller's tmp_path.
  - `BreakingStream` keeps no-op `flush`/`close` because `FileHandler.close` calls both; the real
    file object `break_app_log_stream` displaces is returned so the caller can close it.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import GATE_NAMES, InstrumentLoader
from tests.tools.mem01_verify.result_block_samples import VERSION_KEYS

HEX = "6b" * 32
ORG_ID = UUID("00000000-0000-4000-8000-0000000000aa")
RELEASE_NAME = "step1-gold-v1"
CLEANUP_STEP = 11


class BreakingStream:
    """A log stream whose every write raises the failure it was given."""

    def __init__(self, failure: BaseException) -> None:
        self._failure = failure

    def write(self, text: str) -> int:
        raise self._failure

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


def app_handler() -> logging.Handler:
    """The ONE handler the runner's capture installs on the `app` logger."""
    (handler,) = logging.getLogger("app").handlers
    return handler


def break_app_log_stream(failure: BaseException) -> object:
    """Replace the capture file's stream with one raising `failure`; return the real file."""
    handler = app_handler()
    original = handler.stream  # type: ignore[attr-defined]  # FileHandler
    handler.stream = BreakingStream(failure)  # type: ignore[attr-defined]
    return original


def completed_run_state(
    instrument: InstrumentLoader,
    criteria_path: Path,
    release_dir: Path,
    *,
    run_kind: str = "tuning",
    report_root: Path | None = None,
    run_index: int = 41,
) -> object:
    """A `RunState` complete enough that its completed block passes the stdout schema.

    `report_root` is the hidden root of a checkpoint/validation-shaped run (its report
    directory then lies under `<root>/releases/<name>/reports/<run id>`); a tuning run reports
    under the release directory.
    """
    started = datetime.now(UTC)
    run_id = reference.oracle_run_id(run_index)
    state = instrument("runner_output").RunState(
        run_kind=run_kind, run_id=run_id, started_at=started, partial=False, baseline_label=None
    )
    state.release = instrument("lock").ReleaseInfo(
        path=release_dir,
        name=RELEASE_NAME,
        state="draft" if run_kind == "tuning" else "frozen",
        lock_sha256=HEX,
        manifest={"criteria_sha256": HEX, "files": {}},
        criteria_path=criteria_path,
        visible_files_verified=0,
        hidden_files_verified=0,
    )
    state.criteria = instrument("criteria").load_criteria(criteria_path)
    state.corpus = instrument("corpus_identity").CorpusIdentity(
        version="CORPUS_DIGEST_V1",
        corpus_digest=HEX,
        text_digest=HEX,
        roster_counts={"email_message": 0, "email_attachment": 0},
        taken_at=started,
        snapshot_transaction_id="00000A1B-1",
        database="mem01_probe_oracle",
        host="localhost",
        port=5432,
        org_id=ORG_ID,
    )
    for digest in ("migrations_digest", "fixtures_digest", "code_hash", "config_hash"):
        setattr(state, digest, HEX)
    state.runner_digest = HEX
    state.versions = {key: "1.0" for key in VERSION_KEYS}
    gate_result = instrument("gates.context").GateResult
    state.gate_results = {
        name: gate_result(name=name, status="incomplete", reason="component absent")
        for name in GATE_NAMES
    }
    if report_root is None:
        state.report_dir = release_dir / "reports" / run_id
        state.report_root = release_dir
    else:
        state.report_dir = report_root / "releases" / RELEASE_NAME / "reports" / run_id
        state.report_root = report_root
    state.validation_complete = run_kind == "validation"
    state.step = 9
    return state


async def drive_finish(
    instrument: InstrumentLoader, state: object, argv: list[str], hidden_root: Path
) -> tuple[int, str]:
    """Run steps 10-13 for `state` with the stdout tee over a string; return (exit, transcript)."""
    verify_step1 = instrument("verify_step1")
    out = instrument("runner_render").TeeStream(io.StringIO())
    options = verify_step1.build_parser().parse_args(argv)

    code = await verify_step1._finish(state, options, out, hidden_root, None, None)
    return code, out.transcript


def verdict_lines(text: str) -> list[str]:
    """The §3.8 verdict lines in a transcript (a completed run prints exactly one)."""
    return [line for line in text.splitlines() if line.startswith("STEP1 ")]


def patch_protected_result_writer(
    monkeypatch: pytest.MonkeyPatch, instrument: InstrumentLoader, writer: Callable[..., object]
) -> None:
    """Replace `write_protected_result` under BOTH names the runner can bind it by."""
    monkeypatch.setattr(instrument("runner_logging"), "write_protected_result", writer)
    monkeypatch.setattr(
        instrument("runner_cleanup"), "write_protected_result", writer, raising=False
    )


def failing_after_the_first_write(
    instrument: InstrumentLoader,
) -> tuple[Callable[[Path, object], object], list[Path]]:
    """A `write_protected_result` that lets the step-10 write through and refuses every later one.

    Returns the writer and the list of report directories it was called with, so a test can
    prove the step-12 rewrite was attempted (`len(calls) >= 2`).
    """
    real_write = instrument("runner_logging").write_protected_result
    calls: list[Path] = []

    def write(report_dir: Path, block: object) -> object:
        calls.append(report_dir)
        if len(calls) == 1:
            return real_write(report_dir, block)
        raise OSError("oracle: the final rewrite is refused")

    return write, calls
