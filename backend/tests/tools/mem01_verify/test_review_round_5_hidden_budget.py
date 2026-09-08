"""
Role: Seals fix-registry rows A34, A35 / contract §16.17(h) on the hidden budget — a completed
      outcome's protected result is cached at `<results_root>/hidden_budget.results/<sha256>.json`
      under the HIDDEN root (never beside the visible ledger, where only the digest and the path
      are recorded), a budget constructed without `results_root` refuses to record a completed
      result while a failed outcome is fine, a free repeat verifies the digest (64 lowercase
      hex, refused BEFORE any file is named or opened) and the sha256 of the cached bytes — a
      tampered cache or a traversal-shaped digest refuses the replay with
      `HiddenBudgetLedgerError` — and the RUNNER wires the hidden root in: step 6 constructs its
      budget with `results_root=<hidden root>`.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.hidden_budget, .audit_file, .exceptions, .runner_steps,
      .runner_output, .lock (imported inside each test through the `instrument` loader);
      tests.tools.mem01_verify.reference (ledger reader, hashes, id and stamp forms); pytest
      monkeypatch.
Key invariants:
  - The ledger lives under `<tmp>/gold/` and the results root under `<tmp>/hidden/`: siblings,
    so "nothing under the ledger's directory carries the marker" is a real test of location.
  - The results root EXISTS before the budget is constructed (the runner only ever passes a root
    that is a directory; the budget creates `hidden_budget.results/` itself — §16.17(h)).
  - The traversal test records every `open` AUDIT EVENT the interpreter raises
    (`sys.addaudithook`, installed ONCE per process and gated by a recording flag, since hooks
    cannot be removed), so an open through `builtins.open`, `io.open`, `os.open` or `Path.open`
    alike is seen: a malformed digest may open nothing but the ledger and its `.lock` sibling —
    nothing under the cache directory, never the decoy. The cache directory is NOT pre-created:
    the budget creates it (§16.17(h)).
  - The marker is a nonce inside a nested value of the protected result, never personal data.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import InstrumentLoader

LOCK = hashlib.sha256(b"oracle lock round 5").hexdigest()
QS = hashlib.sha256(b"split QS round 5").hexdigest()
NF = hashlib.sha256(b"split NF round 5").hexdigest()
MARKER = "oracle-round5-protected-marker-4f8c"
DECOY_MARKER = "oracle-round5-decoy-outside-the-cache-0b21"
RESULTS_DIRNAME = "hidden_budget.results"
LEDGER_SIBLINGS = {"hidden_budget.jsonl", "hidden_budget.jsonl.lock"}
RELEASE_NAME = "step1-gold-v1"


def _pair(index: int) -> tuple[str, str]:
    return (
        hashlib.sha256(f"code {index}".encode()).hexdigest(),
        hashlib.sha256(f"config {index}".encode()).hexdigest(),
    )


def _ledger(tmp_path: Path) -> Path:
    """An existing, empty ledger under its own gold root (the state `release cut` leaves)."""
    gold = tmp_path / "gold"
    gold.mkdir(exist_ok=True)
    path = gold / "hidden_budget.jsonl"
    path.write_bytes(b"")
    return path


def _hidden_root(tmp_path: Path) -> Path:
    root = tmp_path / "hidden"
    root.mkdir()
    return root


def _reserve(budget: object, index: int) -> object:
    code, config = _pair(index)
    return budget.reserve(  # type: ignore[attr-defined]
        lock_sha256=LOCK,
        split_digests={"QS": QS, "NF": NF},
        code_hash=code,
        config_hash=config,
        run_id=reference.oracle_run_id(index),
    )


def _events(ledger: Path, event_type: str) -> list[dict]:
    return [event for event in reference.read_jsonl(ledger) if event.get("type") == event_type]


def _block(marker: str, index: int) -> dict:
    """A protected result carrying `marker` in a nested value."""
    return {
        "schema": "MEM01_RESULT_V1",
        "status": "PASS",
        "run_id": reference.oracle_run_id(index),
        "gates": {"SNAP": {"status": "PASS", "note": marker}},
    }


def _files_carrying(root: Path, marker: str) -> list[Path]:
    return [
        path for path in reference.rglob_files(root, "*") if marker.encode() in path.read_bytes()
    ]


def test_a_completed_result_is_cached_under_the_hidden_root_and_never_beside_the_ledger(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    hidden_budget = instrument("hidden_budget")
    ledger, hidden_root = _ledger(tmp_path), _hidden_root(tmp_path)
    budget = hidden_budget.HiddenBudget(ledger, results_root=hidden_root)
    first = _reserve(budget, 11)
    block = _block(MARKER, 11)

    budget.record_outcome(first, outcome="completed", protected_result=block)
    repeat = _reserve(budget, 11)

    assert _files_carrying(ledger.parent, MARKER) == []
    assert {path.name for path in ledger.parent.iterdir()} <= LEDGER_SIBLINGS
    cache = reference.rglob_files(hidden_root / RESULTS_DIRNAME, "*")
    assert len(cache) == 1 and re.fullmatch(r"[0-9a-f]{64}\.json", cache[0].name), cache
    (outcome,) = _events(ledger, "hidden_outcome")
    assert outcome["protected_result_sha256"] == reference.sha256_hex(cache[0].read_bytes())
    assert outcome["protected_result_sha256"] == cache[0].stem
    assert dict(repeat.recorded_result) == block  # type: ignore[attr-defined]


def test_a_completed_result_without_a_results_root_is_refused_while_a_failed_outcome_records(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    hidden_budget = instrument("hidden_budget")
    exceptions = instrument("exceptions")
    ledger = _ledger(tmp_path)
    budget = hidden_budget.HiddenBudget(ledger)
    first = _reserve(budget, 12)
    second = _reserve(budget, 13)

    with pytest.raises(exceptions.HiddenBudgetLedgerError):
        budget.record_outcome(first, outcome="completed", protected_result=_block(MARKER, 12))
    budget.record_outcome(second, outcome="failed", protected_result=None)

    assert _files_carrying(tmp_path, MARKER) == []
    assert [event["outcome"] for event in _events(ledger, "hidden_outcome")] == ["failed"]


def test_a_tampered_cache_file_refuses_the_free_repeat(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    hidden_budget = instrument("hidden_budget")
    exceptions = instrument("exceptions")
    ledger, hidden_root = _ledger(tmp_path), _hidden_root(tmp_path)
    budget = hidden_budget.HiddenBudget(ledger, results_root=hidden_root)
    first = _reserve(budget, 14)
    budget.record_outcome(first, outcome="completed", protected_result=_block(MARKER, 14))
    (cache,) = reference.rglob_files(hidden_root / RESULTS_DIRNAME, "*.json")
    tampered = {**_block(DECOY_MARKER, 14), "tampered": True}
    cache.write_bytes(json.dumps(tampered, sort_keys=True).encode("utf-8"))

    with pytest.raises(exceptions.HiddenBudgetLedgerError):
        _reserve(budget, 14)


class _OpenRecorder:
    """A process-wide audit hook keeping `args[0]` of every `open` event while recording."""

    installed = False
    recording = False
    opened: list[object] = []

    @classmethod
    def hook(cls, event: str, args: tuple[object, ...]) -> None:
        if event == "open" and cls.recording:
            cls.opened.append(args[0])

    @classmethod
    def install_once(cls) -> None:
        if not cls.installed:  # audit hooks cannot be removed: one per process, flag-gated
            sys.addaudithook(cls.hook)
            cls.installed = True


@contextmanager
def _recording_opens() -> Iterator[list[object]]:
    """Record every target the interpreter opens: `open`, `io.open`, `os.open`, `Path.open`."""
    _OpenRecorder.install_once()
    opened: list[object] = []
    _OpenRecorder.opened = opened
    _OpenRecorder.recording = True
    try:
        yield opened
    finally:
        _OpenRecorder.recording = False


def _opened_path(entry: object) -> Path | None:
    """A recorded open target as a resolved path; None for a bare descriptor."""
    if isinstance(entry, int):
        return None
    raw = os.fsdecode(entry) if isinstance(entry, bytes) else str(entry)
    return Path(raw).resolve()  # non-strict: a never-created target still resolves


def test_a_traversal_shaped_digest_is_refused_before_any_file_but_the_ledger_is_opened(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    hidden_budget = instrument("hidden_budget")
    exceptions = instrument("exceptions")
    ledger, hidden_root = _ledger(tmp_path), _hidden_root(tmp_path)
    cache_dir = hidden_root / RESULTS_DIRNAME  # created by the budget, never by the test
    budget = hidden_budget.HiddenBudget(ledger, results_root=hidden_root)
    first = _reserve(budget, 15)
    # the file a traversing `<results_root>/hidden_budget.results/../elsewhere.json` would open
    reference.write_json(hidden_root / "elsewhere.json", _block(DECOY_MARKER, 15))
    instrument("audit_file").append_event(
        ledger,
        {
            "type": "hidden_outcome",
            "reservation_id": first.reservation_id,  # type: ignore[attr-defined]
            "outcome": "completed",
            "protected_result_sha256": "../elsewhere",
            "protected_result_path": None,
            "at": reference.EVENT_AT,
        },
    )
    with _recording_opens() as opened:
        with pytest.raises(exceptions.HiddenBudgetLedgerError):
            _reserve(budget, 15)

    targets = [path for path in map(_opened_path, opened) if path is not None]
    allowed = {ledger.resolve(), ledger.with_name(ledger.name + ".lock").resolve()}
    assert [path for path in targets if path not in allowed] == []  # nothing but the ledger
    assert not any(cache_dir.resolve() in path.parents for path in targets)
    assert not any(path.name == "elsewhere.json" for path in targets)


# ── the runner wiring: step 6 passes the hidden root ─────────────────────────────────────


def test_the_runner_constructs_its_budget_with_the_hidden_root_as_results_root(
    instrument: InstrumentLoader,
    criteria_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner_steps = instrument("runner_steps")
    hidden_budget = instrument("hidden_budget")
    ledger, hidden_root = _ledger(tmp_path), _hidden_root(tmp_path)
    release_dir = ledger.parent / "releases" / RELEASE_NAME
    release_dir.mkdir(parents=True)
    constructions: list[tuple[Path, dict]] = []

    class _SpyBudget(hidden_budget.HiddenBudget):  # type: ignore[misc,name-defined]
        def __init__(self, ledger_path: Path, **kwargs: object) -> None:
            constructions.append((Path(ledger_path), dict(kwargs)))
            super().__init__(ledger_path, **kwargs)

    monkeypatch.setattr(runner_steps, "HiddenBudget", _SpyBudget)
    monkeypatch.setattr(runner_steps, "scorable_hidden_sets", lambda: ("QS",))
    state = instrument("runner_output").RunState(
        run_kind="checkpoint",
        run_id=reference.oracle_run_id(16),
        started_at=datetime.now(UTC),
        partial=False,
        baseline_label=None,
    )
    state.release = instrument("lock").ReleaseInfo(
        path=release_dir,
        name=RELEASE_NAME,
        state="frozen",
        lock_sha256=LOCK,
        manifest={"files": {}},
        criteria_path=criteria_path,
        visible_files_verified=0,
        hidden_files_verified=0,
    )
    state.code_hash, state.config_hash = _pair(16)

    sets = runner_steps.admit_hidden_run(state, hidden_root=hidden_root)

    assert sets == ("QS",)
    assert len(constructions) == 1, constructions
    ledger_path, kwargs = constructions[0]
    assert ledger_path == ledger
    assert kwargs.get("results_root") == hidden_root, kwargs
    assert state.reservation is not None and len(_events(ledger, "hidden_reservation")) == 1
