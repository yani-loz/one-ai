"""
Role: The IDENT gate (participant identity) of contract §11 — it runs the `fixtures.ident_cases`
      batteries through the REAL entity resolver on the run's probe database for ACTUAL identity
      assignments, scores every pair with the pure `score_pair` of §16.11, and measures the two
      corpus invariants of §10.4 over the R6 read-only snapshot. Stage A is expected to FAIL: no
      alias registry exists, so every confirmed alias pair stays unmerged.
Used by: `tools.mem01_verify.gates.registry` (`evaluate`); sealed by
      `tests/tools/mem01_verify/test_gate_scoring.py` (the pure surface) and
      `test_gates_stage_a.py` (alias FAIL with numerator == denominator, the merge count PASS).
Depends on: `tools.mem01_verify.gates.context`, `.criteria`, `.statuses`, `.fixtures.ident_cases`
      (expectations only, R12) and the measured component
      `app.entities.services.entity_resolver.EntityResolver` (ACTUAL results only), reached
      through the probe's REAL write plane.
Key invariants:
  - R12: an expectation is always the fixture's `expected` field; the resolver is only ever run
    to obtain an ACTUAL identity.
  - A stability control's two observations run through TWO independent sessions and resolvers, so
    the second resolution is a real lookup and can never be satisfied by an in-session cache.
  - Every fixture address is resolved in the tenant its record names; the same address seen by two
    orgs yields two tenant-scoped identities (§16.9), which is what the cross-tenant pairs pin.
  - The corpus half reads through `ctx.corpus_snapshot()` only — the fixture half never touches
    the configured database, and the corpus half never writes.
  - Addresses, display names and provenance text never reach stdout, a reason or a diagnostic
    (R5): case rows carry case ids and counts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text

from tools.mem01_verify.criteria import Criterion
from tools.mem01_verify.exceptions import FixtureError
from tools.mem01_verify.fixtures.ident_cases import (
    ALIAS_PAIRS,
    DISTINCT_PAIRS,
    STABILITY_CONTROLS,
)
from tools.mem01_verify.gates.context import (
    CaseVerdict,
    GateContext,
    GateResult,
    criterion_entry,
    incomplete_entries,
    write_gate_report,
)
from tools.mem01_verify.statuses import ERROR, derive_gate_status

GATE = "IDENT"
#: §12: this gate arranges and reads its own fixtures on the run's probe database (`ctx.probe`).
NEEDS_PROBE: bool = True
ALIAS_CRITERION_ID = "ident.provisional.alias_resolution"
DISTINCT_CRITERION_ID = "ident.provisional.no_false_merge"
STABILITY_CRITERION_ID = "ident.provisional.exact_address_stability"
NORMALIZATION_CRITERION_ID = "ident.provisional.c_normalization_key"
MERGE_CRITERION_ID = "ident.provisional.c_no_unconfirmed_merge"

#: The connector provenance the probe resolver records (pinned by the 0014 CHECK to `imap`).
FIXTURE_SOURCE = "imap"
#: The synced mailbox the fixture resolver is bound to (a reserved test domain, §10).
FIXTURE_MAILBOX = "mailbox@example.test"

_GATE_REASON = (
    "confirmed alias pairs stay unmerged because no alias registry exists; the must-remain-"
    "distinct and stability batteries and both corpus invariants are measured"
)
_VALIDATION_REASON = "labeled participant bindings are scored only on the founder validation run"
_NO_PROBE_REASON = "no probe database was opened, so the fixture batteries could not be resolved"
_NO_CORPUS_REASON = "this run never opened the corpus, so the invariant could not be measured (R2)"
_MERGE_REASON = (
    "a person carrying more than one normalized address is a merge; no confirmed-alias "
    "provenance record exists in the schema, so every such merge would be unconfirmed"
)

#: Participations = every recipient row plus every message with a sender address. A participation
#: conforms when its raw as-seen address is retained AND a versioned normalization key (the
#: normalized `person_email` row with its connector provenance) exists for the linked person.
_PARTICIPATIONS_SQL = text(
    "WITH participation AS ("
    " SELECT r.address AS raw_address, EXISTS ("
    "   SELECT 1 FROM person_email pe"
    "   WHERE pe.org_id = r.org_id AND pe.person_id = r.person_id AND pe.source IS NOT NULL"
    " ) AS has_key FROM email_recipient r WHERE r.org_id = :org_id"
    " UNION ALL"
    " SELECT m.from_address, EXISTS ("
    "   SELECT 1 FROM person_email pe"
    "   WHERE pe.org_id = m.org_id AND pe.person_id = m.from_person_id AND pe.source IS NOT NULL"
    " ) FROM email_message m WHERE m.org_id = :org_id AND m.from_address IS NOT NULL"
    ") SELECT count(*) AS participations,"
    " count(*) FILTER (WHERE raw_address IS NULL OR btrim(raw_address) = '') AS missing_raw,"
    " count(*) FILTER (WHERE NOT has_key) AS missing_key,"
    " count(*) FILTER (WHERE raw_address IS NULL OR btrim(raw_address) = '' OR NOT has_key)"
    "   AS defective FROM participation"
)

_MERGES_SQL = text(
    "SELECT count(*) AS merged_people FROM ("
    " SELECT person_id FROM person_email WHERE org_id = :org_id"
    " GROUP BY person_id HAVING count(DISTINCT email) > 1"
    ") merged"
)


class _IdentCase(Protocol):
    """The three IDENT record shapes agree on the four fields `score_pair` reads."""

    case_id: str
    criterion_id: str
    expected: str


def hidden_scorable() -> bool:
    """False in stage A: IDENT's holdout criterion has no labeled evidence yet (§16.13)."""
    return False


def score_pair(pair: _IdentCase, actual_identities: tuple[UUID | None, UUID | None]) -> CaseVerdict:
    """Score one alias / must-remain-distinct / stability record against two actual identities.

    Args:
        pair: The fixture record; its `expected` field is the specification — `same_identity`
            for a confirmed alias, `distinct` for a must-remain-distinct pair, `stable` for an
            exact-address control observed twice.
        actual_identities: The two identities the resolver actually returned. `None` means the
            resolver created no person for that observation (a role or shared mailbox).

    Contract:
        A `same_identity` or `stable` record passes iff both observations resolved AND to the
        same identity. A `distinct` record passes unless BOTH resolved to the SAME identity —
        an address the resolver declines to personify is not a false merge.

    Returns:
        A `CaseVerdict` whose defects name the failure mode, never an address (R5).
    """
    first, second = actual_identities
    if pair.expected == "distinct":
        merged = first is not None and second is not None and first == second
        return CaseVerdict(
            pair.case_id, pair.criterion_id, not merged, ("wrongly_merged",) if merged else ()
        )
    defects: list[str] = []
    if first is None:
        defects.append("first_observation_unresolved")
    if second is None:
        defects.append("second_observation_unresolved")
    if not defects and first != second:
        defects.append("not_resolved_to_one_identity")
    return CaseVerdict(pair.case_id, pair.criterion_id, not defects, tuple(defects))


def _org_id_for(org_key: str) -> UUID:
    """Return the probe tenant a fixture `org_key` names — deterministic, never the corpus org."""
    return uuid5(NAMESPACE_URL, f"mem01/ident/{org_key}")


def _org_keys() -> tuple[str, ...]:
    """Every tenant key the IDENT batteries mention, in a stable order."""
    keys = {pair.org_key for pair in ALIAS_PAIRS}
    keys |= {control.org_key for control in STABILITY_CONTROLS}
    for pair in DISTINCT_PAIRS:
        keys |= {pair.org_key_a, pair.org_key_b}
    return tuple(sorted(keys))


def _addresses_by_org(second_pass: bool) -> dict[str, list[str]]:
    """Group every address the batteries resolve by the tenant key it is observed in.

    Args:
        second_pass: True for the stability battery's SECOND observation, which repeats only the
            stability addresses upper-cased inside angle brackets — a case/whitespace variant
            that normalizes to the same key (§16.9).
    """
    grouped: dict[str, list[str]] = {key: [] for key in _org_keys()}
    for control in STABILITY_CONTROLS:
        variant = f" <{control.address.upper()}> " if second_pass else control.address
        grouped[control.org_key].append(variant)
    if second_pass:
        return grouped
    for pair in ALIAS_PAIRS:
        grouped[pair.org_key].extend((pair.address_a, pair.address_b))
    for pair in DISTINCT_PAIRS:
        grouped[pair.org_key_a].append(pair.address_a)
        grouped[pair.org_key_b].append(pair.address_b)
    return grouped


async def _register_orgs(ctx: GateContext, org_keys: Sequence[str]) -> None:
    """Create the fixture tenants on the probe through the privileged plane (arrangement only)."""
    statement = text(
        "INSERT INTO organizations (id, name, slug) VALUES (:id, :name, :slug)"
        " ON CONFLICT (id) DO NOTHING"
    )
    async with ctx.probe.global_() as session:  # type: ignore[union-attr]  # caller checked
        for key in org_keys:
            org_id = _org_id_for(key)
            await session.execute(
                statement,
                {"id": org_id, "name": f"MEM01 IDENT {key}", "slug": f"mem01-ident-{org_id.hex}"},
            )
        await session.commit()


async def _resolve_in_org(
    ctx: GateContext, org_key: str, addresses: Sequence[str]
) -> dict[str, UUID | None]:
    """Resolve every address of one tenant through a fresh resolver on the REAL write plane."""
    from app.entities.services.email_normalizer import normalize_email
    from app.entities.services.entity_resolver import EntityResolver

    org_id = _org_id_for(org_key)
    resolved: dict[str, UUID | None] = {}
    async with ctx.probe.write(org_id) as session:  # type: ignore[union-attr]  # caller checked
        resolver = EntityResolver(session, mailbox_address=FIXTURE_MAILBOX, source=FIXTURE_SOURCE)
        for address in addresses:
            resolved[normalize_email(address)] = await resolver.resolve_participant(org_id, address)
        await session.commit()
    return resolved


async def _resolve_batteries(ctx: GateContext) -> dict[tuple[str, str], UUID | None]:
    """Resolve every fixture address, then the stability battery's second observation apart.

    Returns:
        `(org_key, normalized address)` → the identity the resolver returned; second-pass results
        are keyed under the `second_pass` marker so they overwrite nothing.
    """
    from app.entities.services.email_normalizer import normalize_email

    identities: dict[tuple[str, str], UUID | None] = {}
    for org_key, addresses in _addresses_by_org(second_pass=False).items():
        for normalized, person_id in (await _resolve_in_org(ctx, org_key, addresses)).items():
            identities[(org_key, normalized)] = person_id
    for org_key, addresses in _addresses_by_org(second_pass=True).items():
        second = await _resolve_in_org(ctx, org_key, addresses)
        for control in STABILITY_CONTROLS:
            if control.org_key == org_key:
                key = normalize_email(control.address)
                identities[("second_pass", f"{org_key}/{key}")] = second.get(key)
    return identities


def _score_batteries(
    identities: Mapping[tuple[str, str], UUID | None],
) -> dict[str, list[CaseVerdict]]:
    """Score the three fixture batteries from the resolved identities, keyed by criterion id."""
    from app.entities.services.email_normalizer import normalize_email

    def seen(org_key: str, address: str) -> UUID | None:
        return identities.get((org_key, normalize_email(address)))

    def again(org_key: str, address: str) -> UUID | None:
        return identities.get(("second_pass", f"{org_key}/{normalize_email(address)}"))

    return {
        ALIAS_CRITERION_ID: [
            score_pair(p, (seen(p.org_key, p.address_a), seen(p.org_key, p.address_b)))
            for p in ALIAS_PAIRS
        ],
        DISTINCT_CRITERION_ID: [
            score_pair(p, (seen(p.org_key_a, p.address_a), seen(p.org_key_b, p.address_b)))
            for p in DISTINCT_PAIRS
        ],
        STABILITY_CRITERION_ID: [
            score_pair(c, (seen(c.org_key, c.address), again(c.org_key, c.address)))
            for c in STABILITY_CONTROLS
        ],
    }


async def _measure_corpus(ctx: GateContext) -> dict[str, int]:
    """Count participations, their defects and multi-address people over the R6 snapshot."""
    parameters = {"org_id": ctx.org_id}
    async with ctx.corpus_snapshot() as session:  # type: ignore[misc]  # caller checked for None
        participations = (await session.execute(_PARTICIPATIONS_SQL, parameters)).one()
        merges = (await session.execute(_MERGES_SQL, parameters)).one()
    return {
        "participations": int(participations.participations),
        "missing_raw_address": int(participations.missing_raw),
        "missing_normalization_key": int(participations.missing_key),
        "defective_participations": int(participations.defective),
        "merged_people": int(merges.merged_people),
    }


def _fixture_entry(criterion: Criterion, verdicts: Sequence[CaseVerdict]) -> dict[str, object]:
    """Build the §3.4 entry of one fixture criterion from its scored records."""
    failed = sum(1 for verdict in verdicts if not verdict.passed)
    return criterion_entry(
        criterion,
        reason=f"{failed}/{len(verdicts)} scored through the real entity resolver on the probe",
        numerator=failed,
        denominator=len(verdicts),
        expected=len(verdicts),
        evaluated=len(verdicts),
    )


def _corpus_entry(criterion: Criterion, counts: Mapping[str, int] | None) -> dict[str, object]:
    """Build the §3.4 entry of one corpus invariant from the measured counts."""
    if counts is None:
        return criterion_entry(criterion, status=ERROR, reason=_NO_CORPUS_REASON)
    if criterion.id == MERGE_CRITERION_ID:
        merged = counts["merged_people"]
        return criterion_entry(
            criterion,
            reason=f"{merged} unconfirmed merges; {_MERGE_REASON}",
            numerator=merged,
            expected=merged,
            evaluated=merged,
        )
    total = counts["participations"]
    defective = counts["defective_participations"]
    return criterion_entry(
        criterion,
        reason=f"{defective}/{total} participations without a raw address or a versioned key",
        numerator=defective,
        denominator=total,
        expected=total,
        evaluated=total,
    )


async def evaluate(ctx: GateContext) -> GateResult:
    """Return the IDENT gate result: fixtures resolved on the probe, invariants over the corpus.

    Args:
        ctx: The run context; `probe` carries the fixture planes and `corpus_snapshot` the R6
            read-only snapshot of the configured corpus.

    Returns:
        A `GateResult` with one entry per IDENT criterion, aggregate diagnostics and the
        `gates/IDENT.json` report holding every `CaseVerdict`.

    Raises:
        FixtureError: The run selected IDENT without a probe database, so no fixture battery
            could be resolved (R2: this is an ERROR, never a smaller denominator).
    """
    if ctx.probe is None:
        raise FixtureError(_NO_PROBE_REASON)
    await _register_orgs(ctx, _org_keys())
    verdicts = _score_batteries(await _resolve_batteries(ctx))
    counts = await _measure_corpus(ctx) if ctx.corpus_snapshot is not None else None
    entries: list[dict[str, object]] = []
    for criterion in ctx.criteria.by_gate.get(GATE, ()):
        if criterion.id in verdicts:
            entries.append(_fixture_entry(criterion, verdicts[criterion.id]))
        elif criterion.split_source == "corpus":
            entries.append(_corpus_entry(criterion, counts))
        else:
            entries.append(incomplete_entries((criterion,), _VALIDATION_REASON)[0])
    status = derive_gate_status(entries)
    scored = [verdict for battery in verdicts.values() for verdict in battery]
    diagnostics: dict[str, object] = {
        "hidden_scorable": hidden_scorable(),
        "alias_pairs": len(verdicts[ALIAS_CRITERION_ID]),
        "distinct_pairs": len(verdicts[DISTINCT_CRITERION_ID]),
        "stability_controls": len(verdicts[STABILITY_CRITERION_ID]),
        "cases_failed": sum(1 for verdict in scored if not verdict.passed),
        "corpus_opened": counts is not None,
        **(counts or {}),
    }
    report = write_gate_report(
        ctx.report_dir,
        name=GATE,
        status=status,
        reason=_GATE_REASON,
        criteria=entries,
        cases=scored,
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


__all__ = [
    "ALIAS_CRITERION_ID",
    "DISTINCT_CRITERION_ID",
    "GATE",
    "MERGE_CRITERION_ID",
    "NORMALIZATION_CRITERION_ID",
    "STABILITY_CRITERION_ID",
    "evaluate",
    "hidden_scorable",
    "score_pair",
]
