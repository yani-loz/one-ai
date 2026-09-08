"""
Role: The IDEM gate (idempotence) of contract §11 — it replays every `fixtures.idem_scenarios`
      script against the REAL `EmailIngestService` on the run's probe database, one isolated
      tenant per scenario, and compares the row deltas of the five tracked tables with the
      independently specified `RowDelta` bounds. The Stage-A replay and exactly-once criteria are
      evaluated; explicit version backfill has no implementation before stage C, so its criterion
      is `incomplete` and the gate with it.
Used by: `tools.mem01_verify.gates.registry` (`evaluate`); sealed by
      `tests/tools/mem01_verify/test_gates_stage_a.py` (replay and exactly-once PASS with a
      denominator of at least ten, backfill `incomplete`).
Depends on: `tools.mem01_verify.gates.context`, `.statuses`, `.exceptions`,
      `.fixtures.idem_scenarios` (scripts and expectations only, R12) and, at call time, the
      application's ingest service, ORM models and the probe's write / platform planes.
Key invariants:
  - R12: every expected delta comes from the fixture's `RowDelta`; the ingest service is only
    ever run to obtain ACTUAL row counts. A `RowDelta` with neither `exact` nor `at_least` is
    DELIBERATELY unconstrained and is never turned into a bound of the evaluator's own.
  - Each scenario runs in its OWN synthetic tenant, so one scenario's rows can never enter
    another's deltas, and every count is filtered by that tenant.
  - Ingest runs through the real write plane exactly as production does; the aborted half of a
    retry rolls the transaction back before commit, so nothing it wrote survives.
  - A concurrent step runs its racers on independent sessions in parallel; a racer that loses the
    dedup race is an expected outcome, not an error, and only the row deltas decide the case.
  - Message bytes, addresses and subjects never reach stdout, a reason or a diagnostic (R5).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text

from tools.mem01_verify.exceptions import FixtureError
from tools.mem01_verify.fixtures.idem_scenarios import (
    BACKFILL_CRITERION,
    EXACTLY_ONCE_CRITERION,
    IDEM_SCENARIOS,
    REPLAY_CRITERION,
    TRACKED_TABLES,
    IdemScenario,
    ScenarioStep,
    build_scenario_payloads,
)
from tools.mem01_verify.gates.context import (
    CaseVerdict,
    GateContext,
    GateResult,
    criterion_entry,
    write_gate_report,
)
from tools.mem01_verify.statuses import INCOMPLETE, derive_gate_status

GATE = "IDEM"
#: §12: this gate arranges and reads its own fixtures on the run's probe database (`ctx.probe`).
NEEDS_PROBE: bool = True
#: The verified-identity namespace the grant writer resolves email participants through.
IDENTITY_SOURCE_TYPE = "email"
#: The connector provenance the probe connection declares.
FIXTURE_SOURCE = "imap"

_GATE_REASON = (
    "replay and exactly-once are exercised against the real ingest service on the probe; "
    "explicit version backfill has no implementation before stage C, so its criterion is "
    "incomplete and the gate with it"
)
_BACKFILL_REASON = "explicit version backfill is a stage-C component and does not exist yet (R3)"
_NO_PROBE_REASON = "no probe database was opened, so no replay scenario could be ingested"

_COUNTS_SQL = text(
    "SELECT "
    + ", ".join(
        f"(SELECT count(*) FROM {table} WHERE org_id = :org_id) AS {table}"
        for table in TRACKED_TABLES
    )
)


def hidden_scorable() -> bool:
    """False in stage A: IDEM carries no hidden-split criterion (§16.13)."""
    return False


def _org_id_for(case_id: str) -> UUID:
    """Return the isolated synthetic tenant one scenario runs in."""
    return uuid5(NAMESPACE_URL, f"mem01/idem/{case_id}")


def _connection_id_for(case_id: str) -> UUID:
    """Return the connector connection one scenario ingests through."""
    return uuid5(NAMESPACE_URL, f"mem01/idem/connection/{case_id}")


async def _prepare(ctx: GateContext, scenario: IdemScenario) -> None:
    """Create the scenario's tenant, its connection and every pre-bound verified identity."""
    from app.access.models.principal_source_identity import PrincipalSourceIdentity
    from app.connectors.models.connector_connection import ConnectorConnection
    from app.entities.models.person import Person, PersonEmail
    from app.entities.services.email_normalizer import normalize_email
    from app.identity.models.organization import Organization

    org_id, connection_id = _org_id_for(scenario.case_id), _connection_id_for(scenario.case_id)
    rows: list[object] = [
        Organization(
            id=org_id, name=f"MEM01 IDEM {scenario.case_id}", slug=f"mem01-idem-{org_id.hex}"
        ),
        ConnectorConnection(
            id=connection_id,
            org_id=org_id,
            connector_type=FIXTURE_SOURCE,
            owner_user_id=None,
            display_name="MEM01 IDEM mailbox",
            auth_method="app_password",
            username="mailbox@acme.test",
            secret_ciphertext=b"\x00" * 32,
            secret_key_version=1,
            config={"host": "mail.acme.test", "port": 993, "use_ssl": True},
            status="configured",
        ),
    ]
    async with ctx.probe.global_() as session:  # type: ignore[union-attr]  # caller checked
        for row in rows:  # one flush per row: the tenant must exist before its connection (FK)
            session.add(row)
            await session.flush()
        for address in scenario.prebound_identities:
            person_id = uuid5(NAMESPACE_URL, f"mem01/idem/{scenario.case_id}/{address}")
            normalized = normalize_email(address)
            session.add(Person(id=person_id, org_id=org_id, is_internal=True))
            await session.flush()
            session.add_all(
                (
                    PersonEmail(
                        org_id=org_id,
                        person_id=person_id,
                        email=normalized,
                        source=FIXTURE_SOURCE,
                    ),
                    PrincipalSourceIdentity(
                        org_id=org_id,
                        person_id=person_id,
                        source_type=IDENTITY_SOURCE_TYPE,
                        external_id=normalized,
                        verified=True,
                    ),
                )
            )
        await session.commit()


async def _counts(ctx: GateContext, org_id: UUID) -> dict[str, int]:
    """Return the current row count of every tracked table inside one scenario's tenant."""
    async with ctx.probe.global_() as session:  # type: ignore[union-attr]  # caller checked
        row = (await session.execute(_COUNTS_SQL, {"org_id": org_id})).one()
    return {table: int(getattr(row, table)) for table in TRACKED_TABLES}


async def _ingest_once(
    ctx: GateContext, scenario: IdemScenario, payload: bytes, *, commit: bool
) -> None:
    """Run one ingest through the real write plane; `commit=False` aborts it before commit."""
    from app.connectors.imap.services.email_ingest_service import EmailIngestService
    from app.connectors.models.connector_connection import ConnectorConnection

    org_id, connection_id = _org_id_for(scenario.case_id), _connection_id_for(scenario.case_id)
    async with ctx.probe.write(org_id) as session:  # type: ignore[union-attr]  # caller checked
        connection = await session.get(ConnectorConnection, connection_id)
        if connection is None:
            raise FixtureError(f"idem: connection missing for {scenario.case_id}")
        await EmailIngestService(session, connection).ingest_email(payload)
        if commit:
            await session.commit()
        else:
            await session.rollback()


async def _ingest_racer(ctx: GateContext, scenario: IdemScenario, payload: bytes) -> str:
    """One racer of a concurrent step: losing the dedup race is an outcome, never an error."""
    try:
        await _ingest_once(ctx, scenario, payload, commit=True)
    except Exception as error:  # noqa: BLE001 - a racer's failure is data, not a gate failure
        return type(error).__name__
    return "committed"


async def _run_step(
    ctx: GateContext, scenario: IdemScenario, step: ScenarioStep, payloads: Mapping[str, bytes]
) -> list[str]:
    """Perform one script verb; return the racer outcomes a concurrent step produced."""
    payload = payloads[step.payload_ref]
    if step.action == "concurrent_duplicate":
        return list(
            await asyncio.gather(
                *(_ingest_racer(ctx, scenario, payload) for _ in range(step.concurrency))
            )
        )
    if step.action == "retry_after_failure":
        await _ingest_once(ctx, scenario, payload, commit=False)
        await _ingest_once(ctx, scenario, payload, commit=True)
        return ["aborted", "committed"]
    await _ingest_once(ctx, scenario, payload, commit=True)
    return ["committed"]


def _delta_defects(
    step: ScenarioStep, before: Mapping[str, int], after: Mapping[str, int]
) -> list[str]:
    """Compare one step's measured deltas with the fixture's bounds; unconstrained tables pass."""
    defects: list[str] = []
    for delta in step.deltas:
        measured = after[delta.table] - before[delta.table]
        if delta.exact is not None and measured != delta.exact:
            defects.append(f"{step.step_id}:{delta.table}:expected_exact_{delta.exact}")
        elif delta.at_least is not None and measured < delta.at_least:
            defects.append(f"{step.step_id}:{delta.table}:below_floor_{delta.at_least}")
    return defects


async def _run_scenario(ctx: GateContext, scenario: IdemScenario) -> tuple[CaseVerdict, list[str]]:
    """Run one whole scenario on its own tenant and score every step's deltas."""
    await _prepare(ctx, scenario)
    payloads = build_scenario_payloads(scenario)
    org_id = _org_id_for(scenario.case_id)
    defects: list[str] = []
    outcomes: list[str] = []
    for step in scenario.steps:
        before = await _counts(ctx, org_id)
        try:
            outcomes.extend(await _run_step(ctx, scenario, step, payloads))
        except Exception as error:  # noqa: BLE001 - a failed step is a case defect (R2 counts it)
            defects.append(f"{step.step_id}:step_raised_{type(error).__name__}")
            continue
        defects.extend(_delta_defects(step, before, await _counts(ctx, org_id)))
    verdict = CaseVerdict(scenario.case_id, scenario.criterion_id, not defects, tuple(defects))
    return verdict, outcomes


def _pins(scenario: IdemScenario, criterion_id: str) -> bool:
    """True when a scenario's denominator entry belongs to `criterion_id`."""
    return scenario.criterion_id == criterion_id or criterion_id in scenario.also_pins


def _entry_for(
    ctx: GateContext,
    criterion_id: str,
    scenarios: Sequence[IdemScenario],
    verdicts: Mapping[str, CaseVerdict],
) -> dict[str, object] | None:
    """Build the §3.4 entry of one Stage-A IDEM criterion from the scenarios that pin it."""
    criterion = next((c for c in ctx.criteria.by_gate.get(GATE, ()) if c.id == criterion_id), None)
    if criterion is None:
        return None
    pinned = [scenario for scenario in scenarios if _pins(scenario, criterion_id)]
    failed = sum(1 for scenario in pinned if not verdicts[scenario.case_id].passed)
    return criterion_entry(
        criterion,
        reason=f"{failed}/{len(pinned)} scenarios changed durable state unexpectedly",
        numerator=failed,
        denominator=len(pinned),
        expected=len(pinned),
        evaluated=len(pinned),
    )


async def evaluate(ctx: GateContext) -> GateResult:
    """Return the IDEM gate result: the Stage-A scripts replayed against the real ingest service.

    Args:
        ctx: The run context; `probe` carries the write and platform planes the scripts run on.

    Returns:
        A `GateResult` whose replay and exactly-once criteria are decided, whose backfill
        criterion is `incomplete` (R3), and whose `gates/IDEM.json` report holds every scenario
        `CaseVerdict`.

    Raises:
        FixtureError: IDEM was selected without a probe database (R2: ERROR, never a smaller
            denominator).
    """
    if ctx.probe is None:
        raise FixtureError(_NO_PROBE_REASON)
    stage_a = [scenario for scenario in IDEM_SCENARIOS if scenario.stage_available == "A"]
    verdicts: dict[str, CaseVerdict] = {}
    outcomes: list[str] = []
    for scenario in stage_a:
        verdict, scenario_outcomes = await _run_scenario(ctx, scenario)
        verdicts[scenario.case_id] = verdict
        outcomes.extend(scenario_outcomes)
    entries: list[dict[str, object]] = []
    for criterion in ctx.criteria.by_gate.get(GATE, ()):
        if criterion.id == BACKFILL_CRITERION:
            entries.append(criterion_entry(criterion, status=INCOMPLETE, reason=_BACKFILL_REASON))
            continue
        entry = _entry_for(ctx, criterion.id, stage_a, verdicts)
        entries.append(
            entry
            if entry is not None
            else criterion_entry(criterion, status=INCOMPLETE, reason=_BACKFILL_REASON)
        )
    status = derive_gate_status(entries)
    diagnostics: dict[str, object] = {
        "hidden_scorable": hidden_scorable(),
        "scenarios_evaluated": len(stage_a),
        "scenarios_failed": sum(1 for verdict in verdicts.values() if not verdict.passed),
        "scenarios_deferred": len(IDEM_SCENARIOS) - len(stage_a),
        "replay_denominator": sum(1 for s in stage_a if _pins(s, REPLAY_CRITERION)),
        "exactly_once_denominator": sum(1 for s in stage_a if _pins(s, EXACTLY_ONCE_CRITERION)),
        "ingest_outcomes": {name: outcomes.count(name) for name in sorted(set(outcomes))},
    }
    report = write_gate_report(
        ctx.report_dir,
        name=GATE,
        status=status,
        reason=_GATE_REASON,
        criteria=entries,
        cases=tuple(verdicts.values()),
        diagnostics=diagnostics,
    )
    return GateResult(
        name=GATE,
        status=status,
        reason=_GATE_REASON,
        criteria=tuple(entries),
        diagnostics=diagnostics,
        report_files=(report,),
    )


__all__ = ["GATE", "evaluate", "hidden_scorable"]
