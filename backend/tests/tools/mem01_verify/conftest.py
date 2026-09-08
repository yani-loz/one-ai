"""
Role: Fixtures for the sealed oracle over tools.mem01_verify — the instrument loader (imports
      happen INSIDE tests so a missing module is an ordinary failure, never a collection error),
      the dev-server reachability skip, the maintenance-database helpers, the session probe
      database with its synthetic corpus, and the re-export of the child-process runners,
      release factories and scenario factories defined in cli_fixtures / scenario_fixtures.
Used by: every test module under tests/tools/mem01_verify/.
Depends on: tests.tools.mem01_verify.session_state, .cli_fixtures, .scenario_fixtures,
      .reference, .seeding; asyncpg (the `postgres` maintenance database and, for the drop
      seal, an owner connection to a probe); pytest-asyncio. The instrument and app.* are
      imported lazily inside factories, never at module level.
Key invariants:
  - Never touches the configured database's tables. Server-side actions are limited to: a TCP
    reachability check; pg_database reads on the `postgres` maintenance database; creating and
    dropping `mem01_probe_*` databases through the instrument; owner connections to probes the
    suite created; and, as the last resort in teardown so no probe is left behind (§14.2 item 7),
    DROP DATABASE of a name carrying the prefix (the suite's cleanup may use FORCE, §16.11).
  - Database work runs on ONE session-scoped event loop (loop_scope="session"), so engines the
    instrument may cache never straddle a closed function loop.
"""

from __future__ import annotations

import importlib
import os
import socket
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
import pytest_asyncio

from tests.tools.mem01_verify import reference, seeding
from tests.tools.mem01_verify.cli_fixtures import (
    BaselinePair,
    BaselinePairFactory,
    DraftRelease,
    DraftReleaseFactory,
    FrozenReleaseFactory,
    baseline_pair,
    cut_arguments,
    draft_release,
    frozen_release,
    instruments_arguments,
    run_cli,
    run_release_cli,
    small_release,
)
from tests.tools.mem01_verify.scenario_fixtures import (
    NO_SCORABLE_REASON,
    FrozenRefusals,
    FrozenRefusalsFactory,
    ValidationRefusals,
    ValidationRefusalsFactory,
    frozen_refusals,
    validation_refusals,
)
from tests.tools.mem01_verify.seeding_rows import SeededCorpus
from tests.tools.mem01_verify.session_state import (
    BACKEND_ROOT,
    CLI_TIMEOUT_SECONDS,
    CRITERIA_PATH,
    GATE_NAMES,
    INSTRUMENT_PACKAGE,
    PROBE_PREFIX,
    RELEASE_NAME,
    REPO_ROOT,
    RUNNER_FOLDER,
    SESSION_LOOP,
    SESSION_STATE,
    CliRunner,
    DevServer,
    InstrumentLoader,
    ProbeCorpusFactory,
)

__all__ = [
    "BACKEND_ROOT",
    "CLI_TIMEOUT_SECONDS",
    "CRITERIA_PATH",
    "GATE_NAMES",
    "INSTRUMENT_PACKAGE",
    "PROBE_PREFIX",
    "RELEASE_NAME",
    "REPO_ROOT",
    "RUNNER_FOLDER",
    "SESSION_LOOP",
    "SESSION_STATE",
    "BaselinePair",
    "BaselinePairFactory",
    "FrozenRefusals",
    "FrozenRefusalsFactory",
    "CliRunner",
    "DevServer",
    "DraftRelease",
    "DraftReleaseFactory",
    "FrozenReleaseFactory",
    "InstrumentLoader",
    "NO_SCORABLE_REASON",
    "ProbeCorpusFactory",
    "ValidationRefusals",
    "ValidationRefusalsFactory",
    "baseline_pair",
    "cut_arguments",
    "frozen_refusals",
    "draft_release",
    "frozen_release",
    "instruments_arguments",
    "run_cli",
    "run_release_cli",
    "small_release",
    "validation_refusals",
]


@pytest.fixture
def instrument() -> InstrumentLoader:
    """Return a loader: `load("verdict")` imports `tools.mem01_verify.verdict` on demand.

    importlib is used deliberately: `from tools.mem01_verify import verdict` would raise a bare
    ImportError on a namespace package, hiding the missing-module reason.
    """

    def load(module_name: str) -> ModuleType:
        return importlib.import_module(f"{INSTRUMENT_PACKAGE}.{module_name}")

    return load


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """The repository root (parent of backend/)."""
    return REPO_ROOT


@pytest.fixture(scope="session")
def backend_root() -> Path:
    """backend/ — the pytest rootdir and the `tools` import root."""
    return BACKEND_ROOT


@pytest.fixture(scope="session")
def runner_folder() -> Path:
    """backend/tools/mem01_verify — the folder runner_sha256 covers."""
    return RUNNER_FOLDER


@pytest.fixture(scope="session")
def criteria_path() -> Path:
    """The draft criteria annex (data the oracle may read, never edit)."""
    return CRITERIA_PATH


@pytest.fixture(scope="session")
def criteria_yaml() -> dict:
    """The annex parsed with pyyaml directly — independent of the instrument's loader."""
    import yaml

    return yaml.safe_load(CRITERIA_PATH.read_text(encoding="utf-8"))


# ── dev server ────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def dev_server() -> DevServer:
    """TCP-check the dev server; skip LOUDLY when unreachable (a skip is never a pass)."""
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = int(os.environ.get("POSTGRES_PORT", "5432"))
    try:
        socket.create_connection((host, port), timeout=3).close()
    except OSError:
        pytest.skip(
            f"dev Postgres at {host}:{port} unreachable — set POSTGRES_HOST/POSTGRES_PORT to the "
            "dev server"
        )
    return DevServer(host=host, port=port)


async def open_owner_connection(server: DevServer, database: str) -> object:
    """Owner asyncpg connection to `database` — the maintenance DB or a probe the suite created.

    Refuses any other target so the configured database can never be opened from here.
    """
    import asyncpg

    from app.core.config import get_settings

    if database != "postgres" and not database.startswith(PROBE_PREFIX):
        raise AssertionError(f"refusing to open a non-probe database {database!r}")
    settings = get_settings()
    return await asyncpg.connect(
        host=server.host,
        port=server.port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        database=database,
    )


async def list_probe_databases(server: DevServer) -> list[str]:
    """Names in pg_database carrying the probe prefix — independent of the instrument."""
    connection = await open_owner_connection(server, "postgres")
    try:
        rows = await connection.fetch(  # type: ignore[attr-defined]
            "SELECT datname FROM pg_database WHERE datname LIKE $1 ORDER BY datname",
            f"{PROBE_PREFIX}%",
        )
        return [row["datname"] for row in rows]
    finally:
        await connection.close()  # type: ignore[attr-defined]


async def drop_probe_database(server: DevServer, name: str) -> None:
    """Last-resort cleanup: DROP DATABASE, only ever for a name carrying the probe prefix."""
    assert name.startswith(PROBE_PREFIX), f"refusing to drop a non-probe database {name!r}"
    connection = await open_owner_connection(server, "postgres")
    try:
        await connection.execute(  # type: ignore[attr-defined]
            f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'
        )
    finally:
        await connection.close()  # type: ignore[attr-defined]


@pytest.fixture
def probe_databases(dev_server: DevServer) -> Callable[[], Awaitable[list[str]]]:
    """Factory: list the `mem01_probe_*` databases that exist right now."""

    async def current() -> list[str]:
        return await list_probe_databases(dev_server)

    return current


@pytest.fixture
def owner_connection(dev_server: DevServer) -> Callable[[str], Awaitable[object]]:
    """Factory: an owner asyncpg connection to a probe the suite created (the caller closes it)."""

    async def connect(database: str) -> object:
        return await open_owner_connection(dev_server, database)

    return connect


@pytest.fixture
def register_probe_for_cleanup(dev_server: DevServer) -> Callable[[str], None]:
    """Record a probe name (e.g. a `--keep-probe` survivor) for teardown to drop."""

    def register(name: str) -> None:
        names = SESSION_STATE.setdefault("extra_probes", set())
        names.add(name)  # type: ignore[attr-defined]

    return register


# ── the session probe database and its synthetic corpus ───────────────────────────────────


def _utc_stamp() -> str:
    """The `<YYYYMMDD>t<HHMMSS>z` prefix of a §16.3 run id for the current instant."""
    return datetime.now(UTC).strftime("%Y%m%dt%H%M%Sz")


@pytest.fixture
def probe_corpus(dev_server: DevServer, instrument: InstrumentLoader) -> ProbeCorpusFactory:
    """Factory: create (once per session) the probe database and seed the three orgs.

    The instrument import and the probe creation happen inside the awaiting test body, so today
    the test FAILS with the missing-module reason instead of erroring in a fixture.
    """

    async def ensure() -> SeededCorpus:
        cached = SESSION_STATE.get("corpus")
        if cached is not None:
            return cached  # type: ignore[return-value]
        probe_db = instrument("probe_db")
        db = instrument("db")
        manager = probe_db.create_probe_database(
            reference.oracle_run_id(int(uuid4().hex[:8], 16), stamp=_utc_stamp())
        )
        probe = await manager.__aenter__()
        SESSION_STATE["probe_manager"] = manager
        SESSION_STATE["probe"] = probe
        sessions = db.probe_session_factories(probe.name)
        corpus = await seeding.seed_corpus(sessions, probe.name)
        SESSION_STATE["corpus"] = corpus
        return corpus

    return ensure


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def _probe_lifecycle() -> object:
    """Drop every probe the session created (through the instrument, then by name)."""
    yield
    manager = SESSION_STATE.pop("probe_manager", None)
    probe = SESSION_STATE.pop("probe", None)
    if manager is not None:
        try:
            await manager.__aexit__(None, None, None)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - cleanup must reach the by-name fallback below
            pass
    extra = SESSION_STATE.pop("extra_probes", set())
    names = set(extra)  # type: ignore[arg-type]
    if probe is not None:
        names.add(probe.name)  # type: ignore[attr-defined]
    if not names:
        return
    host = os.environ.get("POSTGRES_HOST", "localhost")
    server = DevServer(host=host, port=int(os.environ.get("POSTGRES_PORT", "5432")))
    existing = set(await list_probe_databases(server))
    for name in sorted(names & existing):
        await drop_probe_database(server, name)
