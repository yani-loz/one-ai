"""
Role: Seals fix-registry rows A37, A40 / contract §16.17(j)(l) on pure surfaces — (j) for the
      §3.7(b) precondition an admission that carries no terminal event counts as an abort of its
      attempt (`admit A1 → abort A1 → reset A1 → admit A2 → nothing` refuses the next run naming
      A2; `admit A1 → nothing → reset A1` admits A2 exactly once) while `lock_state` is
      unchanged; (l) cancelling `migrate_probe_database` surfaces `asyncio.CancelledError`
      (never a masked `ProbeDatabaseError`) and the Alembic child it spawned is killed AND
      reaped by the instrument itself before the cancellation surfaces. Rows A38/A39 (the roster
      counters) are sealed by `test_review_round_5_pure_b.py`.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.validation_guard, .audit_file, .exceptions, .probe_env (imported
      inside each test through the `instrument` loader); tests.tools.mem01_verify.reference;
      pytest monkeypatch.
Key invariants:
  - The journal events the oracle authors carry exactly the §16.1 keys with `at` in the §16.1
    form.
  - The migration test spawns NO Alembic and reaches NO database: `subprocess.Popen` is replaced
    (under both names the module could bind it by) so the instrument's own spawn launches a
    600-second sleeping Python child with the instrument's own kwargs, and `read_migration_head`
    is replaced by a function that fails the test if it is ever reached. The child is proven
    alive at the moment of cancellation; its reaping is asserted WITHOUT polling (a poll would
    reap it for the instrument); the migration task is always cancelled and awaited, and the
    child killed, in `finally`, so nothing outlives the test.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import InstrumentLoader

LOCK = hashlib.sha256(b"oracle validation lock round 5").hexdigest()
CODE = hashlib.sha256(b"candidate code round 5").hexdigest()
CONFIG = hashlib.sha256(b"candidate config round 5").hexdigest()
FOUNDER = "founder"
SESSION = "oracle-session-round-5"
SLEEPER = "import time; time.sleep(600)"


# ── (j) the validation precondition ──────────────────────────────────────────────────────


def _authorize(instrument: InstrumentLoader, path: Path) -> None:
    instrument("audit_file").append_event(
        path,
        {
            "type": "founder_authorization",
            "lock": LOCK,
            "code_hash": CODE,
            "config_hash": CONFIG,
            "principal": FOUNDER,
            "at": reference.EVENT_AT,
        },
    )


def _reset(instrument: InstrumentLoader, path: Path, attempt_id: str) -> None:
    instrument("audit_file").append_event(
        path,
        {
            "type": "founder_reset",
            "attempt_id": attempt_id,
            "code_hash": CODE,
            "config_hash": CONFIG,
            "principal": FOUNDER,
            "reason": "aborted by a crash before any verdict",
            "at": reference.EVENT_AT,
        },
    )


def _check(instrument: InstrumentLoader, path: Path) -> None:
    instrument("validation_guard").check_validation_preconditions(
        path, lock_sha256=LOCK, code_hash=CODE, config_hash=CONFIG, principal=FOUNDER
    )


def _admit(instrument: InstrumentLoader, path: Path, index: int) -> str:
    return instrument("validation_guard").record_admission(
        path,
        lock_sha256=LOCK,
        code_hash=CODE,
        config_hash=CONFIG,
        principal=FOUNDER,
        session=SESSION,
        run_id=reference.oracle_run_id(index),
    )


def test_an_admission_without_a_terminal_event_counts_as_an_abort_for_the_precondition(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    guard = instrument("validation_guard")
    exceptions = instrument("exceptions")
    audit = tmp_path / "audit.jsonl"
    _authorize(instrument, audit)
    first = _admit(instrument, audit, 1)
    guard.record_abort(audit, first, "interrupted before the verdict")
    _reset(instrument, audit, first)
    _check(instrument, audit)  # positive control: one covered abort admits
    second = _admit(instrument, audit, 2)  # no terminal event follows

    with pytest.raises(exceptions.ValidationRefusedError) as refused:
        _check(instrument, audit)

    assert second in str(refused.value)


def test_a_reset_covering_an_unresolved_admission_admits_exactly_once(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    guard = instrument("validation_guard")
    exceptions = instrument("exceptions")
    audit = tmp_path / "audit.jsonl"
    _authorize(instrument, audit)
    first = _admit(instrument, audit, 1)  # no terminal event: the crash between steps
    _reset(instrument, audit, first)

    _check(instrument, audit)  # the reset covers the unresolved admission
    second = _admit(instrument, audit, 2)
    guard.record_abort(audit, second, "interrupted again")

    with pytest.raises(exceptions.ValidationRefusedError):
        _check(instrument, audit)  # two aborts, one reset (existing rule)


def test_lock_state_after_one_covered_abort_is_still_attempted(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    guard = instrument("validation_guard")
    audit = tmp_path / "audit.jsonl"
    _authorize(instrument, audit)
    first = _admit(instrument, audit, 1)

    guard.record_abort(audit, first, "interrupted before the verdict")
    _reset(instrument, audit, first)

    assert guard.lock_state(audit, LOCK) == "attempted"  # unchanged, sealed elsewhere


# ── (l) the owned migration child ────────────────────────────────────────────────────────


async def _must_not_reach_the_database(name: str) -> str:
    raise AssertionError(f"read_migration_head({name!r}) must not reach the database")


async def test_cancelling_the_migration_kills_and_reaps_the_alembic_child(
    instrument: InstrumentLoader, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe_env = instrument("probe_env")
    original_popen = probe_env.subprocess.Popen
    spawned: list[subprocess.Popen[bytes]] = []

    class _RecordingPopen(original_popen):  # type: ignore[misc,valid-type]
        """The real Popen, recording every reap (`wait`/`communicate`) that RETURNED."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            self.reaps: list[str] = []
            super().__init__(*args, **kwargs)

        def wait(self, *args: object, **kwargs: object) -> int:
            code = super().wait(*args, **kwargs)
            self.reaps.append("wait")
            return code

        def communicate(self, *args: object, **kwargs: object) -> tuple[bytes, bytes]:
            output = super().communicate(*args, **kwargs)
            self.reaps.append("communicate")
            return output

    def sleeping_child(argv: object, *args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        child = _RecordingPopen([sys.executable, "-c", SLEEPER], *args, **kwargs)
        spawned.append(child)
        return child

    monkeypatch.setattr(probe_env.subprocess, "Popen", sleeping_child)
    monkeypatch.setattr(probe_env, "Popen", sleeping_child, raising=False)  # a from-import too
    monkeypatch.setattr(probe_env, "read_migration_head", _must_not_reach_the_database)
    task = asyncio.create_task(
        probe_env.migrate_probe_database("mem01_probe_round5", migration_log=None)
    )
    try:
        for _ in range(100):
            if spawned:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)
        assert spawned and spawned[0].poll() is None, "the child was not alive when cancelled"

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        child = spawned[0]  # asserted WITHOUT polling: a poll would reap it for the instrument
        assert child.returncode is not None, "the migration child outlived its cancellation"
        assert child.reaps, "the instrument killed the child without reaping it"  # type: ignore[attr-defined]
    finally:
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        for child in spawned:
            if child.poll() is None:
                child.kill()
                child.wait()
