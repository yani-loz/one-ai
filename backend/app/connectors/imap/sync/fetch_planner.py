"""
Role: PURE planning helpers for the IMAP incremental fetch — byte-bounded batching, the
      contiguous-cursor advance (stopping at the first unsettled UID), and modified-UTF-7 folder
      decoding. Graduated verbatim from the validated spike (spikes/imap_fetch.py); no IMAP, no I/O,
      directly unit-tested.
Used by: app.connectors.imap.sync.imap_fetcher (batching) + the SyncRunner (cursor advance).
Depends on: stdlib only (base64).
Key invariants:
  - highest_contiguous_uid never returns below `floor` and stops at the FIRST unaccounted UID, so
    the stored cursor can never claim a UID the caller hasn't marked settled (resumability).
  - `accounted` is whatever the CALLER deems settled enough for the cursor to advance over. The
    runner puts ONLY durably STORED/SKIPPED UIDs there — never failed or unreturned ones — so a
    failure or a dropped fetch is an unaccounted gap that STOPS the advance and is re-fetched next
    run (never-lose-mail); a permanently-failing UID thus stalls the folder loudly, by design.
  - batch_by_bytes keeps each batch >= 1 UID even when a single message exceeds the byte target.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterable, Mapping


def batch_by_bytes(uids: list[int], sizes: Mapping[int, int], batch_bytes: int) -> list[list[int]]:
    """Pack UIDs into batches whose total RFC822 size stays near `batch_bytes` (>=1 UID each).

    A UID with NO known size (the server didn't return RFC822.SIZE) is charged the full
    `batch_bytes`, so it forms its own batch instead of collapsing many unknown-size messages into
    one oversized FETCH — the byte bound holds even under a partial size response.
    """
    batches: list[list[int]] = []
    current: list[int] = []
    running = 0
    for uid in uids:
        size = sizes.get(uid, batch_bytes)
        if current and running + size > batch_bytes:
            batches.append(current)
            current, running = [], 0
        current.append(uid)
        running += size
    if current:
        batches.append(current)
    return batches


def highest_contiguous_uid(candidates: list[int], accounted: Iterable[int], floor: int) -> int:
    """Highest UID whose ascending prefix of `candidates` is all in `accounted` (caller-settled).

    Stops at the first not-yet-accounted UID (a gap — a failed ingest or a dropped fetch → retried
    next run); never regresses below `floor` (the current cursor). The resumable per-folder advance.
    """
    seen = set(accounted)
    advanced = floor
    for uid in candidates:
        if uid <= floor:
            continue
        if uid not in seen:
            break
        advanced = uid
    return advanced


def _b64_to_text(chunk: str) -> str:
    """Decode one modified-base64 run; on malformed input return the raw `&chunk-` escape.

    decode_mutf7 is display-only (logs), so a garbled mailbox name must degrade to its raw form, not
    raise (binascii.Error / UnicodeDecodeError) and abort a sync.
    """
    try:
        data = chunk.replace(",", "/")
        data += "=" * (-len(data) % 4)
        return base64.b64decode(data).decode("utf-16-be")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return f"&{chunk}-"


def decode_mutf7(name: str) -> str:
    """Decode an IMAP modified-UTF-7 mailbox name (RFC 3501) to readable Unicode (for logs).

    Never raises: a malformed modified-base64 run falls back to its raw escape (display-only).
    """
    out: list[str] = []
    index = 0
    while index < len(name):
        char = name[index]
        if char == "&":
            end = name.find("-", index)
            if end == -1:
                end = len(name)
            chunk = name[index + 1 : end]
            out.append("&" if chunk == "" else _b64_to_text(chunk))
            index = end + 1
        else:
            out.append(char)
            index += 1
    return "".join(out)
