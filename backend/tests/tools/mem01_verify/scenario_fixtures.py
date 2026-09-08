"""
Role: Session-cached CLI scenarios over the synthetic frozen releases (contract §16.13): the
      Stage A refusal path of `--checkpoint` and `--validation` — every H-split gate reports
      `hidden_scorable() == False`, so the runner must abort with `no scorable hidden set`
      BEFORE any reservation or admission, charging no unit and recording no attempt — plus
      the §3.10 runner-hash refusal at step 3 and the `--gates` partial refusal.
Used by: conftest.py (re-exported as fixtures), test_verify_step1_frozen.py,
      test_verify_step1_validation.py.
Depends on: tests.tools.mem01_verify.cli_fixtures (frozen_release, run_cli, draft_release),
      .frozen_release, .reference, .session_state; pytest.
Key invariants:
  - The frozen variants used here carry TAMPERED hidden hashes (`unopenable`): any hidden open
    would surface as a lock error, so the `no scorable hidden set` reason proves no hidden
    file was touched. The runner-hash variant is untampered and refuses at stage 1.
  - The founder authorization written for the validation scenario is hand-authored JSON (a
    §16.1-exact event), never produced through the instrument.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.cli_fixtures import DraftReleaseFactory, FrozenReleaseFactory
from tests.tools.mem01_verify.frozen_release import FrozenRelease
from tests.tools.mem01_verify.reference import CliRun
from tests.tools.mem01_verify.session_state import SESSION_STATE, CliRunner

NO_SCORABLE_REASON = "no scorable hidden set"
FOUNDER = "founder"


def _checkpoint_arguments(frozen: FrozenRelease, *extra: str) -> list[str]:
    return [
        "--release",
        str(frozen.path),
        "--expect-lock",
        f"sha256:{frozen.lock_sha256()}",
        *extra,
    ]


@dataclass(frozen=True)
class FrozenRefusals:
    """The recorded `--checkpoint` refusals on a Stage A frozen release."""

    frozen: FrozenRelease
    wrong_runner: FrozenRelease
    wrong_runner_run: CliRun
    checkpoint: CliRun
    ledger_after_checkpoint: bytes
    audit_after_checkpoint: bytes
    partial_checkpoint: CliRun
    ledger_after_partial: bytes


FrozenRefusalsFactory = Callable[[], Awaitable[FrozenRefusals]]


@pytest.fixture
def frozen_refusals(
    frozen_release: FrozenReleaseFactory,
    draft_release: DraftReleaseFactory,
    run_cli: CliRunner,
) -> FrozenRefusalsFactory:
    """Factory: run (once per session) the three `--checkpoint` refusals and record them."""

    async def ensure() -> FrozenRefusals:
        cached = SESSION_STATE.get("frozen_refusals")
        if cached is not None:
            return cached  # type: ignore[return-value]
        draft = await draft_release()
        frozen = await frozen_release("test", variant="unopenable")
        wrong = await frozen_release("test", variant="wrong_runner")

        async def run(release: FrozenRelease, *extra: str) -> CliRun:
            return await run_cli(
                _checkpoint_arguments(release, *extra),
                database=draft.database,
                gold_root=release.gold_root,
                hidden_root=release.hidden_root,
            )

        wrong_run = await run(wrong, "--checkpoint")
        checkpoint = await run(frozen, "--checkpoint")
        ledger_after_checkpoint = frozen.ledger_path.read_bytes()
        audit_after_checkpoint = frozen.audit_path.read_bytes()
        partial = await run(frozen, "--gates", "VIS", "--checkpoint")
        scenario = FrozenRefusals(
            frozen=frozen,
            wrong_runner=wrong,
            wrong_runner_run=wrong_run,
            checkpoint=checkpoint,
            ledger_after_checkpoint=ledger_after_checkpoint,
            audit_after_checkpoint=audit_after_checkpoint,
            partial_checkpoint=partial,
            ledger_after_partial=frozen.ledger_path.read_bytes(),
        )
        SESSION_STATE["frozen_refusals"] = scenario
        return scenario

    return ensure


@dataclass(frozen=True)
class ValidationRefusals:
    """The recorded `--validation` refusals on a Stage A frozen release."""

    frozen: FrozenRelease
    unauthorized: CliRun
    audit_after_unauthorized: bytes
    authorization: dict
    authorized: CliRun
    audit_after_authorized: bytes
    ledger_after: bytes


ValidationRefusalsFactory = Callable[[], Awaitable[ValidationRefusals]]


def _authorization_for(frozen: FrozenRelease, aborted_block: dict) -> dict:
    """A §16.1-exact `founder_authorization` naming the lock and the candidate the runner printed
    (placeholders when the abort came before the closure was computed)."""
    return {
        "type": "founder_authorization",
        "event_id": str(uuid4()),
        "at": reference.EVENT_AT,
        "lock": frozen.lock_sha256(),
        "code_hash": aborted_block.get("code_hash") or "0" * 64,
        "config_hash": aborted_block.get("config_hash") or "0" * 64,
        "principal": FOUNDER,
    }


@pytest.fixture
def validation_refusals(
    frozen_release: FrozenReleaseFactory,
    draft_release: DraftReleaseFactory,
    run_cli: CliRunner,
) -> ValidationRefusalsFactory:
    """Factory: `--validation` without and with a founder authorization, both refused (§16.13)."""

    async def ensure() -> ValidationRefusals:
        cached = SESSION_STATE.get("validation_refusals")
        if cached is not None:
            return cached  # type: ignore[return-value]
        draft = await draft_release()
        frozen = await frozen_release("validation", variant="unopenable")

        async def run() -> CliRun:
            return await run_cli(
                _checkpoint_arguments(frozen, "--validation"),
                database=draft.database,
                gold_root=frozen.gold_root,
                hidden_root=frozen.hidden_root,
            )

        unauthorized = await run()
        audit_after_unauthorized = frozen.audit_path.read_bytes()
        block = reference.extract_machine_block(unauthorized.stdout)
        authorization = _authorization_for(frozen, block)
        with frozen.audit_path.open("ab") as handle:
            handle.write(json.dumps(authorization, ensure_ascii=False).encode("utf-8") + b"\n")
        authorized = await run()
        scenario = ValidationRefusals(
            frozen=frozen,
            unauthorized=unauthorized,
            audit_after_unauthorized=audit_after_unauthorized,
            authorization=authorization,
            authorized=authorized,
            audit_after_authorized=frozen.audit_path.read_bytes(),
            ledger_after=frozen.ledger_path.read_bytes(),
        )
        SESSION_STATE["validation_refusals"] = scenario
        return scenario

    return ensure
