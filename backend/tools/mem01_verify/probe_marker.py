"""
Role: The probe-database OWNERSHIP MARKER of contract §16.4 and the staleness classification of
      §16.16(l) — the `mem01_probe_owner` row (`ProbeOwnerMarker`), the reader that fetches it,
      the host-process liveness check, the pure `is_stale` verdict, and the server-wide listing
      of the probes a crashed or `--keep-probe` run left behind.
Used by: tools.mem01_verify.probe_db, which owns the lifecycle (create / claim / migrate / drop)
      and re-exports every name here under §1.3's `probe_db` surface; sealed by
      tests/tools/mem01_verify/test_probe_db_staleness.py and test_probe_db.py.
Depends on: tools.mem01_verify.probe_conn (`OWNER_TABLE`, `MAINTENANCE_DATABASE`,
      `owner_connect`, `fetch_probe_names`, `process_is_alive`), .db (`PROBE_PREFIX`),
      .exceptions (`ProbeDatabaseError`); asyncpg for the driver's error type.
Key invariants:
  - STALE MEANS LEFT BEHIND (§16.16(l)): a probe with any live backend, or one whose creator
    still runs, belongs to a run in flight and is NEVER listed stale. `released` governs REUSE
    only (`probe_db.claim_probe_database`) and never enters the staleness verdict.
  - The listing reads a marker only for probes with ZERO live backends, through a connection
    that lives for one SELECT; the residual instant in which that read can collide with a
    concurrent FORCE-less drop is ACCEPTED (§16.16(s)) — two instrument runs on one server are
    not a supported configuration, and a probe that cannot be opened is omitted, never guessed.
  - A missing marker table means "never claimed", not "server failure": the reader returns None
    (§16.16(s), MARKER BEFORE MIGRATION), so a creation in flight is not mistaken for a leftover.
  - Nothing here creates, migrates, truncates or drops a database; every read goes through
    `probe_conn`, which proves the target carries `PROBE_PREFIX` before it connects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import asyncpg

from tools.mem01_verify.db import PROBE_PREFIX
from tools.mem01_verify.exceptions import ProbeDatabaseError
from tools.mem01_verify.probe_conn import (
    MAINTENANCE_DATABASE,
    OWNER_TABLE,
    fetch_probe_names,
    owner_connect,
    process_is_alive,
)


@dataclass(frozen=True)
class ProbeOwnerMarker:
    """The §16.4 `mem01_probe_owner` row; `released` governs REUSE only, never staleness."""

    run_id: str
    pid: int
    created_at: datetime
    released: bool


def pid_is_alive(pid: int) -> bool:
    """True iff a process with `pid` runs on this host — the liveness half of §16.16(l).

    Portable and dependency-free (psutil is not a dependency); the POSIX/Windows split lives
    once, in `probe_conn.process_is_alive`.
    """
    return process_is_alive(pid)


def is_stale(marker: ProbeOwnerMarker | None, *, pid_alive: bool, live_connections: int) -> bool:
    """True iff a probe was LEFT BEHIND (§16.16(l)) — the pure classifier behind the listing.

    `marker` is the probe's `mem01_probe_owner` row (None when the table or row is absent),
    `pid_alive` whether its creator still runs, `live_connections` the backends connected to it.
    True iff nothing is connected AND (no marker OR its creator is gone): a live backend means a
    run in flight, and `released` never enters the verdict.
    """
    return live_connections == 0 and (marker is None or not pid_alive)


async def read_owner_marker(name: str) -> ProbeOwnerMarker | None:
    """Return the §16.4 ownership marker of a probe, or None when the table or row is absent.

    Raises ProbeDatabaseError when the target is illegitimate or the probe cannot be opened.
    """
    connection = await owner_connect(name)
    try:
        row = await connection.fetchrow(
            f'SELECT run_id, pid, created_at, released FROM "{OWNER_TABLE}"'
        )
    except asyncpg.PostgresError:
        return None  # no marker table yet: an unclaimed probe, not a server failure
    finally:
        await connection.close()
    if row is None:
        return None
    return ProbeOwnerMarker(
        run_id=str(row["run_id"]),
        pid=int(row["pid"]),
        created_at=row["created_at"],
        released=bool(row["released"]),
    )


async def list_stale_probe_databases() -> list[str]:
    """The probes LEFT BEHIND on the configured server (§12/§16.16(l) `stale_probe_databases`).

    A probe with a live backend, or whose creator still runs, belongs to a run in flight and is
    omitted — two consecutive runs therefore agree even while a concurrent run holds a probe of
    its own; what remains is what a crashed or `--keep-probe` run left behind. A probe that
    cannot be opened (it is being dropped underneath us) is omitted, never guessed at; that
    marker read is the accepted residual window of §16.16(s).
    """
    connection = await owner_connect(MAINTENANCE_DATABASE)
    try:
        names = await fetch_probe_names(connection)
        backends = await connection.fetch(
            "SELECT datname, count(*) AS live FROM pg_stat_activity "
            "WHERE datname LIKE $1 GROUP BY datname",
            f"{PROBE_PREFIX}%",
        )
    finally:
        await connection.close()
    live = {record["datname"]: int(record["live"]) for record in backends}
    stale: list[str] = []
    for name in names:
        connections = live.get(name, 0)
        marker = None
        if connections == 0:
            try:
                marker = await read_owner_marker(name)
            except ProbeDatabaseError:
                continue
        alive = marker is not None and pid_is_alive(marker.pid)
        if is_stale(marker, pid_alive=alive, live_connections=connections):
            stale.append(name)
    return stale
