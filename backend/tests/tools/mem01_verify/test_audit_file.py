"""
Role: Seals the append-only event file of contract §1.3 (`audit_file`) and the §16.1 event
      stamps: events are appended, never rewritten; the returned id is the stored `event_id`
      (uuid4) and `at` keeps the §16.1 form; a missing file reads as empty; a torn last line is
      an integrity violation; concurrent appenders never interleave.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.audit_file and .exceptions (imported inside each test);
      tests.tools.mem01_verify.reference (id and stamp forms).
Key invariants:
  - "Append-only" is asserted on BYTES: the file after an append starts with the file before it.
  - Every event authored here is a fully conforming §16.1 event type, so the seals hold whether
    `append_event` validates event shapes or not.
"""

from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import InstrumentLoader

LOCK = hashlib.sha256(b"oracle audit lock").hexdigest()
CODE = hashlib.sha256(b"oracle audit code").hexdigest()
CONFIG = hashlib.sha256(b"oracle audit config").hexdigest()


def _authorization(principal: str) -> dict:
    return {
        "type": "founder_authorization",
        "lock": LOCK,
        "code_hash": CODE,
        "config_hash": CONFIG,
        "principal": principal,
        "at": reference.EVENT_AT,
    }


def _reservation(worker: int, index: int) -> dict:
    return {
        "type": "hidden_reservation",
        "reservation_id": f"res-{worker:02d}-{index:04d}",
        "lock": LOCK,
        "split_digests": {"QS": hashlib.sha256(b"split QS").hexdigest()},
        "code_hash": CODE,
        "config_hash": CONFIG,
        "run_id": reference.oracle_run_id(worker * 1000 + index),
        "at": reference.EVENT_AT,
    }


def test_read_events_on_a_missing_file_is_empty(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    audit_file = instrument("audit_file")

    assert audit_file.read_events(tmp_path / "absent.jsonl") == []


def test_append_returns_the_stored_uuid4_event_id_and_read_preserves_order_and_utf8(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    audit_file = instrument("audit_file")
    path = tmp_path / "audit.jsonl"

    first = audit_file.append_event(path, _authorization("основател"))
    second = audit_file.append_event(path, _reservation(0, 1))
    events = audit_file.read_events(path)

    assert re.fullmatch(reference.UUID4_PATTERN, first) and first != second
    assert [event["event_id"] for event in events] == [first, second]
    assert [event["type"] for event in events] == ["founder_authorization", "hidden_reservation"]
    assert events[0]["principal"] == "основател"
    assert all(re.fullmatch(reference.EVENT_AT_PATTERN, event["at"]) for event in events)
    assert first in path.read_text(encoding="utf-8")


def test_append_only_grows_the_file_by_a_suffix(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    audit_file = instrument("audit_file")
    path = tmp_path / "audit.jsonl"
    audit_file.append_event(path, _reservation(0, 1))
    before = path.read_bytes()

    audit_file.append_event(path, _reservation(0, 2))
    after = path.read_bytes()

    assert after.startswith(before) and len(after) > len(before)
    assert before.endswith(b"\n")


def test_every_line_is_one_json_object_terminated_by_newline(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    audit_file = instrument("audit_file")
    path = tmp_path / "audit.jsonl"
    for index in range(3):
        audit_file.append_event(path, _reservation(0, index))

    lines = path.read_bytes().split(b"\n")

    assert lines[-1] == b"" and len(lines) == 4
    assert [json.loads(line)["reservation_id"] for line in lines[:-1]] == [
        "res-00-0000",
        "res-00-0001",
        "res-00-0002",
    ]


def test_torn_last_line_is_an_integrity_violation(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    audit_file = instrument("audit_file")
    exceptions = instrument("exceptions")
    path = tmp_path / "audit.jsonl"
    audit_file.append_event(path, _authorization("founder"))
    intact = path.read_bytes()
    path.write_bytes(intact + b'{"type": "validation_abort", "at": "2026-')

    with pytest.raises(exceptions.IntegrityViolationError):
        audit_file.read_events(path)
    # positive control: the intact prefix reads back
    path.write_bytes(intact)
    assert [event["type"] for event in audit_file.read_events(path)] == ["founder_authorization"]


def test_concurrent_appenders_never_interleave(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    audit_file = instrument("audit_file")
    path = tmp_path / "audit.jsonl"
    workers, per_worker = 8, 25

    def append_many(worker: int) -> list[str]:
        return [
            audit_file.append_event(path, _reservation(worker, index))
            for index in range(per_worker)
        ]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        ids = [event_id for chunk in pool.map(append_many, range(workers)) for event_id in chunk]

    events = audit_file.read_events(path)
    assert len(events) == workers * per_worker and len(set(ids)) == workers * per_worker
    assert sorted(event["reservation_id"] for event in events) == sorted(
        f"res-{worker:02d}-{index:04d}" for worker in range(workers) for index in range(per_worker)
    )
    assert all(
        event["run_id"]
        == reference.oracle_run_id(
            int(event["reservation_id"][4:6]) * 1000 + int(event["reservation_id"][7:])
        )
        for event in events
    )


def test_integrity_violation_error_is_a_mem01_error(instrument: InstrumentLoader) -> None:
    exceptions = instrument("exceptions")

    assert issubclass(exceptions.IntegrityViolationError, exceptions.Mem01Error)


def test_append_event_stamps_at_when_absent_and_keeps_a_given_at(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    audit_file = instrument("audit_file")
    path = tmp_path / "audit.jsonl"
    unstamped = {key: value for key, value in _reservation(0, 1).items() if key != "at"}

    first = audit_file.append_event(path, unstamped)
    second = audit_file.append_event(path, _reservation(0, 2))
    events = audit_file.read_events(path)

    assert [event["event_id"] for event in events] == [first, second]
    assert re.fullmatch(reference.EVENT_AT_PATTERN, events[0]["at"])
    assert events[1]["at"] == reference.EVENT_AT
    assert set(events[0]) == set(events[1]) == set(_reservation(0, 2)) | {"event_id"}


def test_append_event_refuses_a_preset_event_id_and_a_missing_or_empty_type(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    audit_file = instrument("audit_file")
    exceptions = instrument("exceptions")
    path = tmp_path / "audit.jsonl"
    audit_file.append_event(path, _reservation(0, 1))
    before = path.read_bytes()
    preset = {**_reservation(0, 2), "event_id": "11111111-2222-4333-8444-555555555555"}
    untyped = {key: value for key, value in _reservation(0, 3).items() if key != "type"}
    empty_type = {**_reservation(0, 4), "type": ""}

    for event in (preset, untyped, empty_type):
        with pytest.raises(exceptions.Mem01Error):
            audit_file.append_event(path, event)

    assert path.read_bytes() == before
    assert audit_file.append_event(path, _reservation(0, 5))  # positive control
