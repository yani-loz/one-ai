"""
Role: Seals fix-registry rows A31, A32 / contract §16.17(e)(f) on the runner — (e) a
      `KeyboardInterrupt` or `asyncio.CancelledError` raised by the sequence is an ABORTED run
      (`interrupted: <class>`, probe dropped, aborted block, exit 2, no traceback, no verdict),
      a `KeyboardInterrupt` outside the sequence is one `MEM01 INTERNAL ERROR: KeyboardInterrupt`
      line and exit 2, and `SystemExit` passes through (`--help` exits 0); (f) an argparse usage
      error prints the one fixed line `verify_step1: error: invalid usage; see --help` and never
      echoes the rejected value. Part `_b` seals (g) and (i), part `_c` (g) on hidden run kinds.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.verify_step1 (imported inside each test through the `instrument`
      loader); tests.tools.mem01_verify.reference (block extraction); pytest capfd and
      monkeypatch.
Key invariants:
  - The runner is driven IN PROCESS with `_sequence` / `_guarded_main` monkeypatched, so no
    database, release or hidden root is ever opened; both roots point into tmp_path.
  - `main()` is wrapped so an escaping `BaseException` becomes a plain assertion failure and
    never pytest's session-level KeyboardInterrupt abort.
  - The stub probe's `drop` is idempotent like the real `ProbeDatabase.drop`: step 11 and the
    `finally` of `_run` may both call it, so the seal asserts the EFFECTIVE drop
    (`dropped is True`, at least one call, the lease exited exactly once).
  - The rejected `--org` value is the synthetic `person@example.test`; it must appear on neither
    stream (R5).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import InstrumentLoader

PROBE_NAME = "mem01_probe_20260906t120000z_0a1b2c5d"
REJECTED_VALUE = "person@example.test"
USAGE_ERROR_LINE = "verify_step1: error: invalid usage; see --help"
INTERNAL_ERROR_LINE = "MEM01 INTERNAL ERROR: KeyboardInterrupt"


class _StubProbe:
    """A probe whose drop is recorded; idempotent like the real one (a repeat is a no-op)."""

    name = PROBE_NAME
    owns_lifecycle = True

    def __init__(self) -> None:
        self.drop_calls = 0
        self.dropped = False

    async def drop(self) -> None:
        self.drop_calls += 1
        self.dropped = True


class _StubLease:
    """The open probe context manager the runner closes at step 11."""

    def __init__(self) -> None:
        self.exits = 0

    async def __aexit__(self, *exc: object) -> None:
        self.exits += 1


def _isolate_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MEM01_GOLD_ROOT", str(tmp_path / "gold"))
    monkeypatch.setenv("MEM01_HIDDEN_ROOT", str(tmp_path / "absent-hidden"))


def _drive_main(verify_step1: object, argv: list[str]) -> tuple[int | None, BaseException | None]:
    """Call `main(argv)`; an escaping BaseException is returned, never re-raised into pytest."""
    try:
        return verify_step1.main(argv), None  # type: ignore[attr-defined]
    except BaseException as escaped:  # noqa: BLE001 - the seal asserts nothing escapes
        return None, escaped


def _interrupting_sequence(
    probe: _StubProbe, lease: _StubLease, interruption: BaseException
) -> Callable[..., object]:
    async def sequence(state: object, options: object, observer: object, hidden_root: Path) -> None:
        state.probe, state.lease, state.probe_name = probe, lease, probe.name  # type: ignore[attr-defined]
        state.step = 5  # type: ignore[attr-defined]
        raise interruption

    return sequence


def _verdict_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("STEP1 ")]


# ── (e) interruption ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, asyncio.CancelledError])
def test_an_interruption_inside_the_sequence_is_an_aborted_run_with_the_probe_dropped(
    instrument: InstrumentLoader,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    interruption: type[BaseException],
) -> None:
    verify_step1 = instrument("verify_step1")
    _isolate_roots(monkeypatch, tmp_path)
    probe, lease = _StubProbe(), _StubLease()
    monkeypatch.setattr(
        verify_step1, "_sequence", _interrupting_sequence(probe, lease, interruption())
    )
    capfd.readouterr()

    code, escaped = _drive_main(verify_step1, ["--release", str(tmp_path / "release")])
    captured = capfd.readouterr()

    assert escaped is None, f"{type(escaped).__name__} escaped main()"
    assert code == 2
    assert "Traceback" not in captured.err
    block = reference.extract_machine_block(captured.out)
    assert block["aborted"] is True
    assert block["reason"] == f"interrupted: {interruption.__name__}"
    assert probe.dropped is True and probe.drop_calls >= 1  # the effective drop
    assert lease.exits == 1  # `drop_probe` clears the lease, so a second pass has none to exit
    assert block["cleanup"]["probe_dropped"] is True
    assert _verdict_lines(captured.out) == []


def test_a_keyboard_interrupt_outside_the_sequence_prints_the_internal_error_line(
    instrument: InstrumentLoader, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    verify_step1 = instrument("verify_step1")

    def interrupted(argv: list[str] | None) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(verify_step1, "_guarded_main", interrupted)
    capfd.readouterr()

    code, escaped = _drive_main(verify_step1, [])
    captured = capfd.readouterr()

    assert escaped is None, f"{type(escaped).__name__} escaped main()"
    assert code == 2
    assert [line for line in captured.out.splitlines() if line.strip()] == [INTERNAL_ERROR_LINE]
    assert "Traceback" not in captured.err


def test_help_exits_zero_through_a_system_exit_that_main_lets_propagate(
    instrument: InstrumentLoader, capfd: pytest.CaptureFixture[str]
) -> None:
    verify_step1 = instrument("verify_step1")

    with pytest.raises(SystemExit) as parser_exit:
        verify_step1.build_parser().parse_args(["--help"])
    with pytest.raises(SystemExit) as main_exit:
        verify_step1.main(["--help"])

    assert parser_exit.value.code == 0 and main_exit.value.code == 0
    capfd.readouterr()


# ── (f) value-free usage errors ──────────────────────────────────────────────────────────


def test_an_argparse_usage_error_never_echoes_the_rejected_value(
    instrument: InstrumentLoader, capfd: pytest.CaptureFixture[str]
) -> None:
    verify_step1 = instrument("verify_step1")
    capfd.readouterr()

    with pytest.raises(SystemExit) as usage_exit:
        verify_step1.main(["--org", REJECTED_VALUE])
    captured = capfd.readouterr()

    assert usage_exit.value.code == 2
    assert REJECTED_VALUE not in captured.err and REJECTED_VALUE not in captured.out
    assert "invalid usage" in captured.err
    assert [line for line in captured.err.splitlines() if line.strip()] == [USAGE_ERROR_LINE]
    assert "MEM01_RESULT_V1_BEGIN" not in captured.out
