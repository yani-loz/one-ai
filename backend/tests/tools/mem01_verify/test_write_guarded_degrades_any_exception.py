"""
Role: Seals fix-registry row A22 / contract §16.16(t) — `runner_logging.write_guarded(*writes)`
      performs the artifact writes in order, stops at the first failure and degrades ANY
      exception a write raises to its CLASS NAME (never the message, R5): a `TypeError` exactly
      like an `OSError`, including a `write_protected_result` of a block json cannot serialise,
      which leaves no `protected_result.json` behind.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.runner_logging (imported inside each test); stdlib.
Key invariants:
  - Every deliberate failure carries a sentinel path-like message; the sentinel must never
    appear in the returned detail (only the class name may).
  - No database, no report root: every write targets tmp_path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tests.tools.mem01_verify.conftest import InstrumentLoader

SENTINEL = "secret-path-7a1f"


def _raising(exception: BaseException) -> object:
    def write() -> None:
        raise exception

    return write


def test_a_write_raising_type_error_degrades_to_its_class_name(
    instrument: InstrumentLoader,
) -> None:
    runner_logging = instrument("runner_logging")

    detail = runner_logging.write_guarded(_raising(TypeError(f"{SENTINEL} is not serialisable")))

    assert detail == "TypeError"
    assert SENTINEL not in detail


def test_a_write_raising_os_error_degrades_to_the_raised_class_name(
    instrument: InstrumentLoader,
) -> None:
    runner_logging = instrument("runner_logging")

    plain = runner_logging.write_guarded(_raising(OSError(f"{SENTINEL} cannot be opened")))
    subclass = runner_logging.write_guarded(_raising(PermissionError(f"{SENTINEL} denied")))

    assert plain == "OSError" and subclass == "PermissionError"
    assert SENTINEL not in plain + subclass


def test_an_unserialisable_protected_result_degrades_to_type_error_and_leaves_no_file(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    runner_logging = instrument("runner_logging")
    report_dir = tmp_path / "run"
    serialisable = {"schema": "MEM01_RESULT_V1", "aborted": True, "reason": "oracle"}

    detail = runner_logging.write_guarded(
        lambda: runner_logging.write_protected_result(report_dir, {"a": datetime.now(UTC)})
    )
    control = runner_logging.write_guarded(
        lambda: runner_logging.write_protected_result(tmp_path / "ok", serialisable)
    )

    assert detail == "TypeError"
    assert not (report_dir / "protected_result.json").exists()
    assert control is None and (tmp_path / "ok" / "protected_result.json").is_file()


def test_writes_run_in_order_and_stop_at_the_first_failure_of_any_class(
    instrument: InstrumentLoader,
) -> None:
    runner_logging = instrument("runner_logging")
    calls: list[int] = []

    def step(number: int) -> object:
        def write() -> None:
            calls.append(number)

        return write

    everything = runner_logging.write_guarded(step(1), step(2), step(3))
    after_all = list(calls)
    calls.clear()
    stopped_by_os_error = runner_logging.write_guarded(
        step(1), _raising(OSError(SENTINEL)), step(3)
    )
    after_os_error = list(calls)
    calls.clear()
    stopped_by_type_error = runner_logging.write_guarded(
        step(1), _raising(TypeError(SENTINEL)), step(3)
    )
    after_type_error = list(calls)

    assert everything is None and after_all == [1, 2, 3]
    assert stopped_by_os_error == "OSError" and after_os_error == [1]
    assert stopped_by_type_error == "TypeError" and after_type_error == [1]
    assert runner_logging.write_guarded() is None  # nothing to write is a success
