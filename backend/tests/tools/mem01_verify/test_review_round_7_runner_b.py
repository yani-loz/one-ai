"""
Role: Seals fix-registry row A43 / contract §16.17(d) on the FREE-REPEAT path —
      `_finish_free_repeat` reads `app_log_emit_failures()` (and the capture's close result)
      BEFORE it replays: with a real capture on a checkpoint-shaped state that already recorded
      an `OSError` emit failure and a probe that drops cleanly, the replay is refused — the
      printed block is ABORTED at step 11 with reason `artifacts_unwritable: app_log OSError`,
      exit 2, no verdict line, and nothing is recorded on the ledger; a `TypeError` emit failure
      leaves the recorded block replayed verbatim (exit per its recorded status, no diagnostics
      expected in it); a failing drop plus an `OSError` emit failure aborts as `cleanup_failed`
      (precedence).
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.verify_step1, .runner_logging, .runner_output, .runner_render,
      .hidden_budget, .exceptions, .lock, .criteria, .corpus_identity, .gates.context (imported
      inside each test through the `instrument` loader); tests.tools.mem01_verify
      .review_round_5_harness (unmodified), .reference and .result_block_samples.
Key invariants:
  - The emit failure is proven to sit on the handler BEFORE the drive
    (`app_log_emit_failures()` equals the expected class tuple), so a runner that reads only
    after closing the capture cannot pass.
  - "Nothing recorded" is observed on a REAL ledger holding this run's reservation: the ledger's
    events before and after the drive are identical.
  - The recorded block is the sealed all-PASS checkpoint sample, so a genuine replay exits 0 and
    the aborted projection is unmistakable; every marker is a nonce and reaches the handler only
    through `args`; the real file object the broken stream displaces is closed by the test.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import logging
from collections.abc import Callable
from pathlib import Path

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify import review_round_5_harness as harness
from tests.tools.mem01_verify.conftest import InstrumentLoader
from tests.tools.mem01_verify.result_block_samples import completed_block, make_all_pass

PROBE_NAME = "mem01_probe_20260906t120000z_0a1b2c5d"
MARKER = "oracle-round7-free-repeat-marker-9e2b"
NO_SPACE = (28, "no space left on device")
QS = hashlib.sha256(b"split QS round 7 free repeat").hexdigest()


class _StubProbe:
    """A probe whose drop is recorded (and, when asked, refused)."""

    name = PROBE_NAME
    owns_lifecycle = True

    def __init__(self, refuse_with: BaseException | None = None) -> None:
        self.drop_calls = 0
        self.dropped = False
        self._refuse_with = refuse_with

    async def drop(self) -> None:
        self.drop_calls += 1
        if self._refuse_with is not None:
            raise self._refuse_with
        self.dropped = True


def _os_error_emit_failure() -> object:
    displaced = harness.break_app_log_stream(OSError(*NO_SPACE))
    logging.getLogger("app.round7").warning("%s", MARKER)
    return displaced


def _formatting_emit_failure() -> object:
    logging.getLogger("app.round7").warning("%s %s", MARKER)  # too few args: TypeError
    return None


async def _drive_free_repeat(
    instrument: InstrumentLoader,
    criteria_path: Path,
    tmp_path: Path,
    *,
    probe: _StubProbe,
    inject: Callable[[], object],
    expected_failures: tuple[str, ...],
) -> tuple[int, str, list[dict], list[dict]]:
    """Replay the recorded all-PASS checkpoint with a real capture carrying an emit failure.

    Returns (exit code, transcript, ledger events before, ledger events after).
    """
    verify_step1 = instrument("verify_step1")
    runner_logging = instrument("runner_logging")
    gold_root, hidden_root = tmp_path / "gold", tmp_path / "hidden"
    release_dir = gold_root / "releases" / harness.RELEASE_NAME
    release_dir.mkdir(parents=True)
    hidden_root.mkdir()
    ledger = gold_root / "hidden_budget.jsonl"
    ledger.write_bytes(b"")
    state = harness.completed_run_state(
        instrument, criteria_path, release_dir, run_kind="checkpoint", report_root=hidden_root
    )
    state.probe, state.probe_name = probe, probe.name  # type: ignore[attr-defined]
    budget = instrument("hidden_budget").HiddenBudget(ledger, results_root=hidden_root)
    state.reservation = budget.reserve(  # type: ignore[attr-defined]
        lock_sha256=harness.HEX,
        split_digests={"QS": QS},
        code_hash=harness.HEX,
        config_hash=harness.HEX,
        run_id=state.run_id,  # type: ignore[attr-defined]
    )
    state.budget = budget  # type: ignore[attr-defined]
    state.hidden = budget.counters({"QS": QS}, lock_sha256=harness.HEX)  # type: ignore[attr-defined]
    stack = contextlib.ExitStack()
    stack.enter_context(runner_logging.capture_app_logging(state.report_dir))  # type: ignore[attr-defined]
    state.log_capture = stack  # type: ignore[attr-defined]
    displaced = inject()
    assert runner_logging.app_log_emit_failures() == expected_failures  # harness: it sits there
    before = reference.read_jsonl(ledger)
    recorded = make_all_pass(completed_block("checkpoint"), keep_reason=False)
    out = instrument("runner_render").TeeStream(io.StringIO())
    try:
        code = await verify_step1._finish_free_repeat(state, out, hidden_root, recorded)
    finally:
        if displaced is not None:
            displaced.close()  # type: ignore[attr-defined]
        stack.close()  # a no-op once the runner closed it
    return code, out.transcript, before, reference.read_jsonl(ledger)


async def test_a_free_repeat_with_an_os_error_emit_failure_is_aborted_and_records_nothing(
    instrument: InstrumentLoader, criteria_path: Path, tmp_path: Path
) -> None:
    probe = _StubProbe()

    code, transcript, before, after = await _drive_free_repeat(
        instrument,
        criteria_path,
        tmp_path,
        probe=probe,
        inject=_os_error_emit_failure,
        expected_failures=("OSError",),
    )

    assert probe.dropped is True  # harness control: the drop itself was clean
    block = reference.extract_machine_block(transcript)
    assert block["aborted"] is True, "the replay went ahead despite an OSError emit failure"
    assert block["aborted_at_step"] == harness.CLEANUP_STEP
    assert str(block["reason"]).startswith("artifacts_unwritable: app_log OSError")
    assert harness.verdict_lines(transcript) == []
    assert code == 2
    assert after == before  # nothing recorded on the ledger


async def test_a_free_repeat_with_a_type_error_emit_failure_replays_the_recorded_block(
    instrument: InstrumentLoader, criteria_path: Path, tmp_path: Path
) -> None:
    probe = _StubProbe()

    code, transcript, before, after = await _drive_free_repeat(
        instrument,
        criteria_path,
        tmp_path,
        probe=probe,
        inject=_formatting_emit_failure,
        expected_failures=("TypeError",),
    )

    block = reference.extract_machine_block(transcript)
    assert block["aborted"] is False and block["status"] == "PASS"  # evidence only
    assert block["run_id"] == completed_block("checkpoint")["run_id"]  # the RECORDED block
    assert "app_log_emit_failures" not in str(block.get("diagnostics", {}))  # verbatim replay
    assert harness.verdict_lines(transcript) == []  # the replay prints the recorded block alone
    assert code == 0
    assert after == before


async def test_a_failing_drop_still_wins_over_an_os_error_emit_failure(
    instrument: InstrumentLoader, criteria_path: Path, tmp_path: Path
) -> None:
    exceptions = instrument("exceptions")
    refusing = _StubProbe(refuse_with=exceptions.ProbeDatabaseError("backends still connected"))

    code, transcript, before, after = await _drive_free_repeat(
        instrument,
        criteria_path,
        tmp_path,
        probe=refusing,
        inject=_os_error_emit_failure,
        expected_failures=("OSError",),
    )

    assert refusing.drop_calls >= 1  # harness control: the drop was attempted and refused
    block = reference.extract_machine_block(transcript)
    assert block["aborted"] is True
    assert block["aborted_at_step"] == harness.CLEANUP_STEP
    assert str(block["reason"]).startswith("cleanup_failed")  # precedence over the emit failure
    assert harness.verdict_lines(transcript) == []
    assert code == 2
    assert after == before
