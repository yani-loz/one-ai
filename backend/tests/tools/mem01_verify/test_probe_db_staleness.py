"""
Role: Seals fix-registry row A12 / §16.16(l) — the pure staleness classifier
      `probe_db.is_stale(marker, *, pid_alive, live_connections)`: a probe is stale iff nothing
      is connected to it AND (its `mem01_probe_owner` marker is missing OR its creator process
      is dead). A live creator or any live connection is never stale (a markerless probe with
      connections is a creation in flight), and the marker's `released` flag — which governs
      §16.4 reuse only — never changes the verdict. The marker record is
      `ProbeOwnerMarker(run_id: str, pid: int, created_at: datetime, released: bool)`.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.probe_db (imported inside each test); tests.tools.mem01_verify
      .reference (run-id forms).
Key invariants:
  - Every liveness input is a plain argument; no test touches a process table or a server.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import InstrumentLoader

MARKER_FIELDS = {"run_id", "pid", "created_at", "released"}
CREATED_AT = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)
# (released, pid_alive, live_connections) -> stale?  — the registry row's marker cases
MARKER_CASES = [
    pytest.param(False, False, 0, True, id="unreleased_dead_creator_no_connections_is_stale"),
    pytest.param(False, True, 0, False, id="unreleased_live_creator_is_not_stale"),
    pytest.param(False, False, 1, False, id="unreleased_dead_creator_one_connection_is_not_stale"),
    pytest.param(True, False, 0, True, id="released_dead_creator_no_connections_is_stale"),
    pytest.param(True, True, 0, False, id="released_live_creator_is_not_stale"),
    pytest.param(True, False, 1, False, id="released_one_connection_is_not_stale"),
]
LIVENESS_GRID = [(False, 0), (True, 0), (False, 1), (True, 2)]


def _marker(instrument: InstrumentLoader, *, released: bool, pid: int = 4242) -> object:
    return instrument("probe_db").ProbeOwnerMarker(
        run_id=reference.oracle_run_id(9), pid=pid, created_at=CREATED_AT, released=released
    )


def test_a_markerless_probe_is_stale_only_while_nothing_is_connected(
    instrument: InstrumentLoader,
) -> None:
    probe_db = instrument("probe_db")

    assert probe_db.is_stale(None, pid_alive=False, live_connections=0) is True
    assert probe_db.is_stale(None, pid_alive=True, live_connections=0) is True  # no pid to trust
    assert probe_db.is_stale(None, pid_alive=False, live_connections=2) is False  # in flight


@pytest.mark.parametrize(("released", "pid_alive", "live_connections", "stale"), MARKER_CASES)
def test_marker_cases_classify_by_connections_and_creator_liveness(
    instrument: InstrumentLoader,
    released: bool,
    pid_alive: bool,
    live_connections: int,
    stale: bool,
) -> None:
    probe_db = instrument("probe_db")
    marker = _marker(instrument, released=released)

    verdict = probe_db.is_stale(marker, pid_alive=pid_alive, live_connections=live_connections)

    assert verdict is stale


@pytest.mark.parametrize(("pid_alive", "live_connections"), LIVENESS_GRID)
def test_the_released_flag_never_changes_the_verdict(
    instrument: InstrumentLoader, pid_alive: bool, live_connections: int
) -> None:
    probe_db = instrument("probe_db")

    verdicts = {
        probe_db.is_stale(
            _marker(instrument, released=released),
            pid_alive=pid_alive,
            live_connections=live_connections,
        )
        for released in (False, True)
    }

    assert len(verdicts) == 1
    assert verdicts == {live_connections == 0 and not pid_alive}


def test_liveness_inputs_are_keyword_only(instrument: InstrumentLoader) -> None:
    probe_db = instrument("probe_db")
    marker = _marker(instrument, released=False)

    assert probe_db.is_stale(marker, pid_alive=False, live_connections=0) is True
    with pytest.raises(TypeError):
        probe_db.is_stale(marker, False, 0)


def test_probe_owner_marker_is_a_frozen_record_of_the_four_marker_columns(
    instrument: InstrumentLoader,
) -> None:
    probe_db = instrument("probe_db")

    fields = {field.name for field in dataclasses.fields(probe_db.ProbeOwnerMarker)}
    marker = _marker(instrument, released=True, pid=7)

    assert fields == MARKER_FIELDS
    assert probe_db.ProbeOwnerMarker.__dataclass_params__.frozen
    assert (marker.pid, marker.released, marker.created_at) == (7, True, CREATED_AT)  # type: ignore[attr-defined]
