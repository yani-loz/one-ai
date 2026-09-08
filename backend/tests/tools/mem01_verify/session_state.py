"""
Role: Session-wide constants, type aliases, the dev-server descriptor and the mutable session
      state shared by the oracle's conftest and its fixture modules (cli_fixtures,
      scenario_fixtures) — kept apart so those modules can import them without importing
      conftest (no circular import).
Used by: conftest.py, cli_fixtures.py, scenario_fixtures.py; re-exported to the test modules by
      conftest.py.
Depends on: tests.tools.mem01_verify.reference (CliRun); pytest, pytest-asyncio marks; stdlib.
Key invariants:
  - `SESSION_STATE` is the ONLY place session-cached objects live (probe, corpus, releases,
    scenario runs); every factory reading it is idempotent and lazy (test-env brief §5).
  - `SESSION_LOOP` marks every DB-backed async test so all instrument engines share one loop.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Literal

import pytest

from tests.tools.mem01_verify.reference import CliRun
from tests.tools.mem01_verify.seeding_rows import SeededCorpus

INSTRUMENT_PACKAGE = "tools.mem01_verify"
BACKEND_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = BACKEND_ROOT.parent
RUNNER_FOLDER = BACKEND_ROOT / "tools" / "mem01_verify"
CRITERIA_PATH = RUNNER_FOLDER / "release" / "criteria.step1.v1.yaml"
RELEASE_NAME = "step1-gold-v1"
PROBE_PREFIX = "mem01_probe_"
GATE_NAMES = (
    "QS",
    "CH",
    "NF",
    "LANG",
    "IDEM",
    "VIS",
    "ERASE",
    "RET",
    "COV",
    "FID",
    "THR",
    "TIME",
    "IDENT",
    "RED",
    "ATTR",
    "SNAP",
    "EMB",
)
H_SPLIT_SETS = ("QS", "NF", "LANG", "RET")
CLI_TIMEOUT_SECONDS = 1200.0

SESSION_STATE: dict[str, object] = {}

# Every DB-backed async test carries this mark so all instrument engines share ONE loop.
SESSION_LOOP = pytest.mark.asyncio(loop_scope="session")

InstrumentLoader = Callable[[str], ModuleType]
ProbeCorpusFactory = Callable[[], Awaitable[SeededCorpus]]
CliForm = Literal["module", "script"]
CliRunner = Callable[..., Awaitable[CliRun]]


@dataclass(frozen=True)
class DevServer:
    """Where the configured Postgres server listens (from the oracle command's environment)."""

    host: str
    port: int
