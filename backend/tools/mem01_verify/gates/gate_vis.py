"""
Role: The VIS gate (visibility) of contract §11 — it loads the `fixtures.vis_matrix` org x
      persona x state arrangement onto the run's probe database through the REAL write and
      platform planes (grants as `acl_grant` rows, org-visible messages with the
      `visibility_promotion` lineage PF-01 demands), then runs every probe through the REAL
      person-bound reader plane and scores it with the pure `score_probe` of §16.11. The gate is
      `incomplete` in Stage A because thread expansion and vector search have no plane yet.
Used by: `tools.mem01_verify.gates.registry` (`evaluate`); sealed by
      `tests/tools/mem01_verify/test_gate_scoring.py` (the pure surface) and
      `test_gates_stage_a.py` (three probe criteria PASS, `vis.route_state_coverage` incomplete).
Depends on: `tools.mem01_verify.gates.context`, `.criteria`, `.statuses`, `.exceptions`,
      `.fixtures.vis_matrix` (expectations only, R12) and, at call time, the application ORM
      models plus the probe's three planes (`app.*` imported lazily inside the loader).
Key invariants:
  - No privileged connection ever plays the reader (§12): arrangement runs on the write and
    platform planes, every probe runs through `reader(org_id, person_id)`, and the row it sees is
    decided by RLS — probe statements filter on the primary key or the lexical marker, never on
    `org_id`. A pooled-reuse probe opens and closes a predecessor session first, so the target
    session very likely reuses that backend and must rebind the person GUC (reported per run).
  - An org-visible message is loaded restricted-origin WITH its promotion lineage (approver row,
    audit row, `visibility_promotion` row) — the 0019 lineage guard would otherwise refuse it.
  - `vis.route_state_coverage` is `incomplete`, never PASS: thread-expansion and vector-search
    cells have no plane before stages C and D (R3), and both routes are named in the diagnostics.
  - Subjects, bodies, addresses and filenames never reach stdout, a reason or a diagnostic (R5).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text

from tools.mem01_verify.exceptions import FixtureError
from tools.mem01_verify.fixtures.vis_matrix import (
    ALLOWED,
    DENIED,
    NO_PERSON,
    STAGE_A_ROUTES,
    MessageSpec,
    Probe,
    VisMatrix,
    build_vis_matrix,
)
from tools.mem01_verify.gates.context import (
    CaseVerdict,
    GateContext,
    GateResult,
    criterion_entry,
    write_gate_report,
)
from tools.mem01_verify.statuses import INCOMPLETE, derive_gate_status

GATE = "VIS"
#: §12: this gate arranges and reads its own fixtures on the run's probe database (`ctx.probe`).
NEEDS_PROBE: bool = True
COVERAGE_CRITERION_ID = "vis.route_state_coverage"

#: The two routes whose statement differs from a plain primary-key read.
LEXICAL_ROUTE = "lexical_search"
CARRIER_ROUTE = "attachment_metadata"
#: The instant a revoked grant's tombstone carries.
REVOKED_AT = datetime(2026, 1, 1, tzinfo=UTC)
#: The only `source` value `ck_person_email_source` (migration 0014) lets a person_email carry.
FIXTURE_SOURCE = "imap"

_GATE_REASON = (
    "the three Stage-A routes (direct_read, lexical_search, attachment_metadata) are probed with "
    "positive and negative controls on the real reader plane; the thread_expansion (stage C) and "
    "vector_search (stage D) cells have no plane yet, so route_state_coverage is incomplete"
)
_COVERAGE_REASON = (
    "thread_expansion and vector_search have no implementation before stages C and D, so their "
    "cells carry no probe and the coverage ratio cannot be decided (R3)"
)
_NO_PROBE_REASON = "no probe database was opened, so the visibility matrix could not be loaded"

_DIRECT_SQL: Mapping[str, object] = {
    table: text(f"SELECT 1 FROM {table} WHERE id = :target_id")
    for table in ("email_message", "email_recipient", "email_attachment")
}
_CARRIER_SQL = text(
    "SELECT filename, content_type, size_bytes, content_hash, extraction_status"
    " FROM email_attachment WHERE id = :target_id"
)
#: FK order for the platform rows: the app declares no `relationship()`, so the unit of work
#: would otherwise insert them in mapper (class-name) order and violate the tenant foreign keys.
PLATFORM_INSERT_ORDER = ("Organization", "User", "AuditLog", "ConnectorConnection")

_LEXICAL_SQL = text("SELECT 1 FROM email_message WHERE body_text LIKE :pattern")
_TARGET_KINDS: Mapping[str, str] = {
    "email_message": "message",
    "email_recipient": "recipient",
    "email_attachment": "attachment",
}
_BACKEND_PID_SQL = text("SELECT pg_backend_pid()")
_WARMUP_SQL = text("SELECT count(*) FROM email_message")


def score_probe(probe: Probe, observed: Literal["allowed", "denied"]) -> CaseVerdict:
    """Score one visibility probe against the outcome the reader plane produced (§16.11, pure).

    Args:
        probe: The fixture record; `expected` is `allowed` or `denied`, authored from the rule in
            its `origin` and never from observing a reader.
        observed: What the read attempt returned — `allowed` when the row came back.

    Contract:
        Passes iff the observed outcome equals the specified one: a negative probe that returned
        a row is a forbidden disclosure, a positive probe that returned nothing is a missing
        allowed row (deny-all is a FAIL, never a pass).

    Returns:
        A `CaseVerdict` naming the failure direction — never a row value (R5).
    """
    if observed == probe.expected:
        return CaseVerdict(probe.case_id, probe.criterion_id, True, ())
    defect = "forbidden_row_returned" if probe.expected == DENIED else "allowed_row_missing"
    return CaseVerdict(
        probe.case_id, probe.criterion_id, False, (f"{defect}:{probe.route}:{probe.state}",)
    )


def _fixture_id(kind: str, key: str) -> UUID:
    """Return the deterministic probe-database id one fixture key stands for."""
    return uuid5(NAMESPACE_URL, f"mem01/vis/{kind}/{key}")


def _platform_rows(matrix: VisMatrix) -> list[object]:
    """Build the organizations, approver users, connections and audit rows the matrix needs."""
    from app.connectors.models.connector_connection import ConnectorConnection
    from app.identity.models.audit_log import AuditLog
    from app.identity.models.organization import Organization
    from app.identity.models.user import User

    rows: list[object] = [
        Organization(id=org.org_id, name=org.name, slug=f"mem01-vis-{org.org_key.lower()}")
        for org in matrix.orgs
    ]
    for message in matrix.messages:
        if message.promotion is None:
            continue
        approver = message.promotion.approved_by_user_id
        org_id = _org_id_of(matrix, message.org_key)
        rows += [
            User(
                id=approver,
                org_id=org_id,
                role="company_admin",
                email=f"approver-{approver.hex[:8]}@example.test",
                full_name="MEM01 VIS approver",
                password_hash="mem01-fixture-not-a-credential",
            ),
            AuditLog(
                id=_fixture_id("audit", message.message_key),
                actor_type="user",
                actor_id=approver,
                action="visibility.promote",
                org_id=org_id,
                entity_type="email_message",
                entity_id=_fixture_id("message", message.message_key),
            ),
        ]
    rows += [
        ConnectorConnection(
            id=_fixture_id("connection", org.org_key),
            org_id=org.org_id,
            connector_type="imap",
            owner_user_id=None,
            display_name="MEM01 VIS mailbox",
            auth_method="app_password",
            username=f"mailbox@{org.domain}",
            secret_ciphertext=b"\x00" * 32,
            config={"host": f"mail.{org.domain}", "port": 993, "use_ssl": True},
            secret_key_version=1,
            status="configured",
        )
        for org in matrix.orgs
    ]
    return rows


def _org_id_of(matrix: VisMatrix, org_key: str) -> UUID:
    """Return the tenant id one fixture org key names."""
    return next(org.org_id for org in matrix.orgs if org.org_key == org_key)


def _message_rows(matrix: VisMatrix, message: MessageSpec) -> tuple[list[object], list[object]]:
    """Build one message's promotion lineage plus its message, children and grant rows."""
    from app.access.models.acl_grant import AclGrant
    from app.access.models.visibility_promotion import VisibilityPromotion
    from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient

    org_id, connection_id = (
        _org_id_of(matrix, message.org_key),
        _fixture_id("connection", message.org_key),
    )
    message_id = _fixture_id("message", message.message_key)
    persons = {persona.persona_key: persona.person_id for persona in matrix.personas}
    lineage: list[object] = []
    if message.promotion is not None:
        lineage.append(
            VisibilityPromotion(
                org_id=org_id,
                object_type="email_message",
                object_id=message_id,
                from_scope=message.promotion.from_scope,
                to_scope=message.promotion.to_scope,
                approved_by_user_id=message.promotion.approved_by_user_id,
                audit_log_id=_fixture_id("audit", message.message_key),
            )
        )
    scopes = {
        "org_id": org_id,
        "visibility_scope": message.visibility_scope,
        "origin_scope": "restricted",
        "container_id": connection_id,
    }
    rows: list[object] = [
        EmailMessage(
            id=message_id,
            connection_id=connection_id,
            headers={},
            parse_status="parsed",
            dedup_key=f"mem01-vis-{message.message_key}",
            message_id=message.message_id,
            subject=message.subject,
            body_text=message.body_text,
            from_address=message.from_address,
            from_person_id=persons.get(message.from_persona_key or ""),
            **scopes,
        )
    ]
    rows += [
        EmailRecipient(
            id=_fixture_id("recipient", recipient.recipient_key),
            email_id=message_id,
            kind=recipient.kind,
            address=recipient.address,
            name=recipient.display_name,
            person_id=persons.get(recipient.persona_key or ""),
            **scopes,
        )
        for recipient in message.recipients
    ]
    rows += [
        EmailAttachment(
            id=_fixture_id("attachment", attachment.attachment_key),
            email_id=message_id,
            filename=attachment.filename,
            content_type=attachment.content_type,
            size_bytes=attachment.size_bytes,
            content_hash=attachment.content_hash,
            is_inline=attachment.is_inline,
            extracted_text=attachment.extracted_text,
            extraction_status=attachment.extraction_status,
            extractor_name=attachment.extractor_name,
            extractor_version=attachment.extractor_version,
            **scopes,
        )
        for attachment in message.attachments
    ]
    rows += [
        AclGrant(
            org_id=org_id,
            person_id=persons[grant.persona_key],
            object_type="email_message",
            object_id=message_id,
            connection_id=connection_id,
            provenance=grant.provenance,
            revoked_at=REVOKED_AT if grant.revoked else None,
        )
        for grant in message.grants
    ]
    return lineage, rows


async def _arrange(ctx: GateContext, matrix: VisMatrix) -> None:
    """Load the whole matrix onto the probe: platform rows first, then one pass per tenant."""
    from app.entities.models.person import Person, PersonEmail

    platform = _platform_rows(matrix)
    async with ctx.probe.global_() as session:  # type: ignore[union-attr]  # caller checked
        for stage_type in PLATFORM_INSERT_ORDER:
            batch = [row for row in platform if type(row).__name__ == stage_type]
            if batch:
                session.add_all(batch)
                await session.flush()
        await session.commit()
    for org in matrix.orgs:
        people: list[object] = []
        for persona in (p for p in matrix.personas if p.org_key == org.org_key):
            people += [
                Person(id=persona.person_id, org_id=org.org_id, is_internal=True),
                PersonEmail(
                    org_id=org.org_id,
                    person_id=persona.person_id,
                    email=persona.address,
                    source=FIXTURE_SOURCE,
                ),
            ]
        lineage: list[object] = []
        content: list[object] = []
        for message in (m for m in matrix.messages if m.org_key == org.org_key):
            message_lineage, message_rows = _message_rows(matrix, message)
            lineage.extend(message_lineage)
            content.extend(message_rows)
        parents = [row for row in content if type(row).__name__ == "EmailMessage"]
        children = [row for row in content if type(row).__name__ != "EmailMessage"]
        async with ctx.probe.write(org.org_id) as session:  # type: ignore[union-attr]
            for stage in (people, lineage, parents, children):
                if stage:
                    session.add_all(stage)
                    await session.flush()
            await session.commit()


def _reader_identity(matrix: VisMatrix, reader: str, org_key: str) -> tuple[UUID, UUID | None]:
    """Return the `(org_id, person_id)` a reader session opens with; a no-person read binds None."""
    if reader == NO_PERSON:
        return _org_id_of(matrix, org_key), None
    persona = next(p for p in matrix.personas if p.persona_key == reader)
    return _org_id_of(matrix, org_key), persona.person_id


def _probe_statement(probe: Probe) -> tuple[object, dict[str, object]]:
    """Return the statement and parameters one probe's route runs (no org filter — RLS decides)."""
    if probe.route == LEXICAL_ROUTE:
        return _LEXICAL_SQL, {"pattern": f"%{probe.lexical_query}%"}
    target_id = _fixture_id(_TARGET_KINDS[probe.target_kind], probe.target_key)
    statement = _CARRIER_SQL if probe.route == CARRIER_ROUTE else _DIRECT_SQL[probe.target_kind]
    return statement, {"target_id": target_id}


async def _observe(
    ctx: GateContext, matrix: VisMatrix, probe: Probe
) -> tuple[Literal["allowed", "denied"], bool | None]:
    """Run one probe through the real reader plane; report the outcome and connection reuse.

    Returns:
        The read outcome, and — for a pooled-reuse probe only — whether the target session landed
        on the SAME backend the predecessor persona just used (`None` otherwise). The flag is a
        diagnostic the verdict never depends on, but a cell that never actually reused a
        connection has not exercised the rebinding it claims to.
    """
    predecessor_backend: int | None = None
    if probe.pooled_predecessor is not None:
        predecessor = next(p for p in matrix.personas if p.persona_key == probe.pooled_predecessor)
        async with ctx.probe.reader(  # type: ignore[union-attr]
            _org_id_of(matrix, predecessor.org_key), predecessor.person_id
        ) as warm:
            await warm.execute(_WARMUP_SQL)
            predecessor_backend = await warm.scalar(_BACKEND_PID_SQL)
    org_id, person_id = _reader_identity(matrix, probe.reader, probe.reader_org_key)
    statement, parameters = _probe_statement(probe)
    async with ctx.probe.reader(org_id, person_id) as session:  # type: ignore[union-attr]
        rows = (await session.execute(statement, parameters)).fetchall()  # type: ignore[arg-type]
        backend = await session.scalar(_BACKEND_PID_SQL)
    reused = None if predecessor_backend is None else predecessor_backend == backend
    return (ALLOWED if rows else DENIED), reused


def _coverage_diagnostics(matrix: VisMatrix) -> dict[str, object]:
    """Aggregate the route x state matrix: which cells are controlled, which have no plane yet."""
    open_cells = [c for c in matrix.cells if not (c.allowed_count and c.denied_count)]
    return {
        "cells_total": len(matrix.cells),
        "cells_with_positive_and_negative": len(matrix.cells) - len(open_cells),
        "cells_unevaluated": len(open_cells),
        "routes_evaluated": list(STAGE_A_ROUTES),
        "routes_without_a_plane": sorted({cell.route for cell in open_cells}),
    }


def _entries(
    ctx: GateContext, matrix: VisMatrix, verdicts: Sequence[CaseVerdict]
) -> list[dict[str, object]]:
    """Build one §3.4 entry per VIS criterion from the scored probes and the coverage matrix."""
    entries: list[dict[str, object]] = []
    for criterion in ctx.criteria.by_gate.get(GATE, ()):
        if criterion.id == COVERAGE_CRITERION_ID:
            entries.append(criterion_entry(criterion, status=INCOMPLETE, reason=_COVERAGE_REASON))
            continue
        scored = [verdict for verdict in verdicts if verdict.criterion_id == criterion.id]
        failed = sum(1 for verdict in scored if not verdict.passed)
        entries.append(
            criterion_entry(
                criterion,
                reason=f"{failed}/{len(scored)} probes on the real person-bound reader plane",
                numerator=failed,
                denominator=len(scored),
                expected=len(scored),
                evaluated=len(scored),
            )
        )
    return entries


async def evaluate(ctx: GateContext) -> GateResult:
    """Return the VIS gate result: the matrix loaded and probed on the probe database.

    Args:
        ctx: The run context; `probe` carries the three real planes the matrix uses.

    Returns:
        A `GateResult` whose three probe criteria are decided, whose coverage criterion is
        `incomplete`, and whose `gates/VIS.json` report holds every probe `CaseVerdict`.

    Raises:
        FixtureError: VIS was selected without a probe database (R2: ERROR, never a smaller
            denominator).
    """
    if ctx.probe is None:
        raise FixtureError(_NO_PROBE_REASON)
    matrix = build_vis_matrix()
    await _arrange(ctx, matrix)
    verdicts: list[CaseVerdict] = []
    reuse_flags: list[bool] = []
    for probe in matrix.probes:
        observed, reused = await _observe(ctx, matrix, probe)
        verdicts.append(score_probe(probe, observed))
        if reused is not None:
            reuse_flags.append(reused)
    entries = _entries(ctx, matrix, verdicts)
    diagnostics: dict[str, object] = {
        "probes": len(verdicts),
        "probes_failed": sum(1 for verdict in verdicts if not verdict.passed),
        "pooled_probes": len(reuse_flags),
        "pooled_probes_on_a_reused_backend": sum(reuse_flags),
        "personas": len(matrix.personas),
        "orgs": len(matrix.orgs),
        **_coverage_diagnostics(matrix),
    }
    status = derive_gate_status(entries)
    report = write_gate_report(
        ctx.report_dir,
        name=GATE,
        status=status,
        reason=_GATE_REASON,
        criteria=entries,
        cases=verdicts,
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


__all__ = ["COVERAGE_CRITERION_ID", "GATE", "evaluate", "score_probe"]
