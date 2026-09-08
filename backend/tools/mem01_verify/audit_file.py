"""
Role: The append-only JSONL event file behind every audit trail and ledger of contract §16.1 —
      an ENVELOPE writer (§16.12) that stamps `event_id` and `at`, appends exactly one JSON line
      atomically, and never truncates or rewrites; plus the reader that refuses a torn file.
Used by: tools.mem01_verify.validation_guard (the validation journal), .hidden_budget (the
      cross-release budget ledger), .release (the release audit), and the sealed oracle module
      tests/tools/mem01_verify/test_audit_file.py.
Depends on: tools.mem01_verify.exceptions (IntegrityViolationError); stdlib json/os/threading/uuid.
Key invariants:
  - Append-only ON BYTES: after a successful append the file's previous bytes are an exact
    prefix of the new bytes; the file is never opened for writing without O_APPEND.
  - A refused event leaves the file byte-identical (validation happens before any file I/O, so
    a refusal does not even create the file).
  - This module validates the ENVELOPE only (`type` present and non-empty, `event_id` absent).
    The type-specific key sets of §16.1 are the responsibility of the modules that write them.
  - A caller-supplied `at` is preserved verbatim; an absent `at` is stamped ISO-8601 UTC to
    second precision with a `Z` suffix.
  - Every line is one JSON object followed by a single `\n`; a file that does not end with a
    newline, or any line that is not a JSON object, is an IntegrityViolationError.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from tools.mem01_verify.exceptions import IntegrityViolationError

_APPEND_LOCK = threading.Lock()
_APPEND_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
_FILE_MODE = 0o644


def _utc_stamp(now: datetime) -> str:
    """Render `now` as the §16.1 event stamp: ISO-8601 UTC, second precision, `Z` suffix."""
    return now.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_envelope(event: Mapping[str, object]) -> dict[str, object]:
    """Validate the envelope of `event` and return the stamped object to append.

    Raises:
        IntegrityViolationError: If `type` is missing, not a string, or empty; or if the caller
            already supplied an `event_id` (only this module mints ids).
    """
    event_type = event.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise IntegrityViolationError(
            "an audit event needs a non-empty string 'type'; refusing to append"
        )
    if "event_id" in event:
        raise IntegrityViolationError(
            "an audit event must not carry a preset 'event_id'; refusing to append"
        )
    stamped: dict[str, object] = dict(event)
    stamped["event_id"] = str(uuid4())
    if "at" not in stamped:
        stamped["at"] = _utc_stamp(datetime.now(UTC))
    return stamped


def _append_line(path: Path, payload: bytes) -> None:
    """Append `payload` to `path` as one atomic O_APPEND write, then flush it to disk.

    Raises:
        IntegrityViolationError: If the operating system accepted only part of the line, which
            would leave a torn record behind.
    """
    with _APPEND_LOCK:
        descriptor = os.open(path, _APPEND_FLAGS, _FILE_MODE)
        try:
            written = os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    if written != len(payload):
        raise IntegrityViolationError(
            f"partial append to {path.name}: wrote {written} of {len(payload)} bytes"
        )


def append_event(path: Path, event: Mapping[str, object]) -> str:
    """Append one event to the append-only JSONL file at `path` and return its event id.

    The event is stamped with a fresh uuid4 `event_id` and, when absent, an `at` timestamp in
    the §16.1 form; every other key is written through unchanged. The line is appended with
    O_APPEND under a process-wide lock, so concurrent appenders never interleave and the file
    is never truncated or rewritten.

    Args:
        path: The JSONL file to append to; it is created when missing (parents must exist).
        event: The event body. Must carry a non-empty string `type` and must NOT carry
            `event_id`. May carry `at`, which is preserved verbatim.

    Returns:
        The generated `event_id` as stored in the appended line.

    Raises:
        IntegrityViolationError: If the envelope is refused (missing/empty `type`, preset
            `event_id`) — in which case the file is untouched — or if the append was partial.
        OSError: If the file cannot be opened or written.
    """
    stamped = _build_envelope(event)
    payload = (json.dumps(stamped, ensure_ascii=False) + "\n").encode("utf-8")
    _append_line(path, payload)
    return str(stamped["event_id"])


def _decode(path: Path, data: bytes) -> str:
    """Decode the file's bytes as strict UTF-8, reporting a bad byte as a torn file."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IntegrityViolationError(f"{path.name} is not valid UTF-8: {exc}") from exc


def read_events(path: Path) -> list[dict]:
    """Read every event from the append-only JSONL file at `path`, in file order.

    Args:
        path: The JSONL file. A missing file reads as an empty list (nothing has been appended
            yet); an existing empty file reads as an empty list too.

    Returns:
        The events as dictionaries, in the order they were appended.

    Raises:
        IntegrityViolationError: If the file does not end with a newline (a torn last line), if
            any line is not valid JSON, or if any line is not a JSON object.
    """
    if not path.exists():
        return []
    data = path.read_bytes()
    if not data:
        return []
    if not data.endswith(b"\n"):
        raise IntegrityViolationError(
            f"{path.name} does not end with a newline — the last event is torn"
        )
    events: list[dict] = []
    for number, line in enumerate(_decode(path, data).splitlines(), start=1):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            raise IntegrityViolationError(f"{path.name} line {number} is not JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise IntegrityViolationError(f"{path.name} line {number} is not a JSON object")
        events.append(parsed)
    return events
