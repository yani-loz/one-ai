"""
Role: Seals the pure scoring surfaces of contract §16.11 with hand-made ACTUAL results — every
      `score_*` call returns a `CaseVerdict` for its case, passes on the fixture's own expectation
      and fails, with a defect, on a deviation the oracle constructs (R12: the expected side is
      the fixture record, never a measured component).
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.gates.gate_{time,red,snap,ident,vis,cov,fid}, .gates.context,
      .evid_norm, .fixtures.* (imported inside each test); the criteria annex (scope policy)
      through pyyaml.
Key invariants:
  - Fixture records are taken from the landed batteries by their expectation shape; the oracle
    never constructs a fixture record of its own.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tests.tools.mem01_verify.conftest import InstrumentLoader

VERDICT_FIELDS = {"case_id", "criterion_id", "passed", "defects"}


def _check_verdict(verdict: object, case: object, *, passed: bool) -> None:
    assert verdict.case_id == case.case_id  # type: ignore[attr-defined]
    assert verdict.criterion_id == case.criterion_id  # type: ignore[attr-defined]
    assert verdict.passed is passed  # type: ignore[attr-defined]
    assert isinstance(verdict.defects, tuple)  # type: ignore[attr-defined]
    if passed:
        assert verdict.defects == ()  # type: ignore[attr-defined]
    else:
        assert verdict.defects and all(isinstance(d, str) and d for d in verdict.defects)  # type: ignore[attr-defined]


def test_case_verdict_is_a_frozen_dataclass_with_the_four_fields(
    instrument: InstrumentLoader,
) -> None:
    context = instrument("gates.context")

    fields = {field.name for field in dataclasses.fields(context.CaseVerdict)}

    assert fields == VERDICT_FIELDS
    assert context.CaseVerdict.__dataclass_params__.frozen  # type: ignore[attr-defined]


def test_time_score_case_passes_on_the_fixture_instant_and_fails_off_by_one_hour(
    instrument: InstrumentLoader,
) -> None:
    gate_time = instrument("gates.gate_time")
    cases = [c for c in instrument("fixtures.time_cases").TIME_CASES if "instant_utc" in c.expected]
    assert len(cases) >= 5

    for case in cases[:5]:
        instant = datetime.fromisoformat(case.expected["instant_utc"].replace("Z", "+00:00"))
        _check_verdict(gate_time.score_case(case, instant), case, passed=True)
        _check_verdict(gate_time.score_case(case, instant + timedelta(hours=1)), case, passed=False)
        _check_verdict(gate_time.score_case(case, None), case, passed=False)
        assert instant.tzinfo is not None and instant.utcoffset() == datetime.now(UTC).utcoffset()


def test_red_score_canary_passes_on_a_placeholder_everywhere_and_fails_on_any_survivor(
    instrument: InstrumentLoader,
) -> None:
    gate_red = instrument("gates.gate_red")
    canary = instrument("fixtures.red_cases").RED_POSITIVES[0]
    text = canary.text_builder()
    start, end = canary.canary_span
    assert text[start:end] == canary.canary_text  # positive control on the fixture itself
    redacted = f"{text[:start]}[REDACTED:{canary.secret_class}]{text[end:]}"
    clean = dict.fromkeys(canary.surfaces, redacted)
    survivor_surface = canary.surfaces[-1]
    partial = (
        f"{text[:start]}[REDACTED]{canary.canary_text[len(canary.canary_text) // 2 :]}{text[end:]}"
    )

    passed = gate_red.score_canary(canary, clean)
    survived = gate_red.score_canary(canary, {**clean, survivor_surface: text})
    half = gate_red.score_canary(canary, {**clean, survivor_surface: partial})

    _check_verdict(passed, canary, passed=True)
    _check_verdict(survived, canary, passed=False)
    _check_verdict(half, canary, passed=False)


def test_red_score_negative_passes_when_the_control_survives_and_fails_when_it_is_masked(
    instrument: InstrumentLoader,
) -> None:
    gate_red = instrument("gates.gate_red")
    control = instrument("fixtures.red_cases").RED_NEGATIVES[0]
    start, end = control.control_span
    assert control.text[start:end] == control.control_text  # positive control on the fixture
    untouched = dict.fromkeys(control.surfaces, control.text)
    masked = f"{control.text[:start]}[REDACTED:number]{control.text[end:]}"

    kept = gate_red.score_negative(control, untouched)
    over = gate_red.score_negative(control, {**untouched, control.surfaces[0]: masked})

    _check_verdict(kept, control, passed=True)
    _check_verdict(over, control, passed=False)


def test_snap_score_case_passes_on_the_fixture_resolution_and_fails_on_a_shifted_span(
    instrument: InstrumentLoader,
) -> None:
    gate_snap = instrument("gates.gate_snap")
    evid_norm = instrument("evid_norm")
    cases = instrument("fixtures.snap_cases").SNAP_CASES
    resolved = next(
        c for c in cases if c.expected.kind == "resolved" and len(c.expected.spans) == 1
    )
    unresolved = next(c for c in cases if c.expected.kind == "unresolved")
    span = resolved.expected.spans[0]
    exact = evid_norm.Resolution(
        kind="resolved", spans=(evid_norm.Span(**dataclasses.asdict(span)),), reason=""
    )
    shifted = evid_norm.Resolution(
        kind="resolved",
        spans=(
            evid_norm.Span(
                span.scalar_start + 1, span.scalar_end + 1, span.byte_start + 1, span.byte_end + 1
            ),
        ),
        reason="",
    )
    missing = evid_norm.Resolution(kind="unresolved", spans=(), reason="no_unit_aligned_occurrence")

    _check_verdict(gate_snap.score_case(resolved, exact), resolved, passed=True)
    _check_verdict(gate_snap.score_case(resolved, shifted), resolved, passed=False)
    _check_verdict(gate_snap.score_case(resolved, missing), resolved, passed=False)
    _check_verdict(
        gate_snap.score_case(
            unresolved,
            evid_norm.Resolution(kind="unresolved", spans=(), reason=unresolved.expected.reason),
        ),
        unresolved,
        passed=True,
    )
    _check_verdict(gate_snap.score_case(unresolved, exact), unresolved, passed=False)


def test_ident_score_pair_on_alias_distinct_and_stability_records(
    instrument: InstrumentLoader,
) -> None:
    gate_ident = instrument("gates.gate_ident")
    ident_cases = instrument("fixtures.ident_cases")
    alias, distinct = ident_cases.ALIAS_PAIRS[0], ident_cases.DISTINCT_PAIRS[0]
    stable = ident_cases.STABILITY_CONTROLS[0]
    same, other = uuid4(), uuid4()

    _check_verdict(gate_ident.score_pair(alias, (same, same)), alias, passed=True)
    _check_verdict(gate_ident.score_pair(alias, (same, other)), alias, passed=False)
    _check_verdict(gate_ident.score_pair(alias, (None, None)), alias, passed=False)
    _check_verdict(gate_ident.score_pair(distinct, (same, other)), distinct, passed=True)
    _check_verdict(gate_ident.score_pair(distinct, (same, same)), distinct, passed=False)
    _check_verdict(gate_ident.score_pair(stable, (same, same)), stable, passed=True)
    _check_verdict(gate_ident.score_pair(stable, (same, other)), stable, passed=False)


def test_vis_score_probe_passes_only_when_the_observed_outcome_matches(
    instrument: InstrumentLoader,
) -> None:
    gate_vis = instrument("gates.gate_vis")
    probes = instrument("fixtures.vis_matrix").build_vis_matrix().probes
    allowed = next(p for p in probes if p.expected == "allowed")
    denied = next(p for p in probes if p.expected == "denied")

    _check_verdict(gate_vis.score_probe(allowed, "allowed"), allowed, passed=True)
    _check_verdict(gate_vis.score_probe(allowed, "denied"), allowed, passed=False)
    _check_verdict(gate_vis.score_probe(denied, "denied"), denied, passed=True)
    _check_verdict(gate_vis.score_probe(denied, "allowed"), denied, passed=False)


def _same_disposition(actual: object, expected: object) -> bool:
    return actual == expected or getattr(actual, "value", actual) == getattr(
        expected, "value", expected
    )


def test_cov_dispose_and_score_scenario_follow_the_frozen_policy(
    instrument: InstrumentLoader, criteria_yaml: dict
) -> None:
    gate_cov = instrument("gates.gate_cov")
    scenarios = instrument("fixtures.cov_scenarios").COV_SCENARIOS
    policy = criteria_yaml["scope_policy"]
    dispositions = {s.expected.disposition for s in scenarios}
    assert len(dispositions) >= 3

    for scenario in scenarios:
        disposition = gate_cov.dispose(scenario, policy)
        assert _same_disposition(disposition, scenario.expected.disposition), scenario.case_id
        _check_verdict(gate_cov.score_scenario(scenario, disposition), scenario, passed=True)
        wrong = next(d for d in dispositions if not _same_disposition(d, disposition))
        _check_verdict(gate_cov.score_scenario(scenario, wrong), scenario, passed=False)


def _simple_fid_cases(cases: Sequence[object]) -> dict[str, object]:
    """One case per format whose expectation is plain ordered units — no tables, links, negation
    guards, pages, sheets or layout columns — so a passing text can be rendered from the
    fixture's own units under §16.13's representation rules (units joined in order)."""
    chosen: dict[str, object] = {}
    for case in cases:
        expected = case.expected  # type: ignore[attr-defined]
        if expected.row_groups or expected.link_pairs or expected.negation_guards:
            continue
        if any(
            unit.page is not None or unit.sheet is not None or unit.layout_column is not None
            for unit in expected.units
        ):
            continue
        if len(expected.ordered_sequences) > 1 or len(expected.units) < 2:
            continue
        chosen.setdefault(case.format, case)  # type: ignore[attr-defined]
    return chosen


def _ordered_units(case: object) -> list[object]:
    expected = case.expected  # type: ignore[attr-defined]
    if not expected.ordered_sequences:
        return list(expected.units)
    by_id = {unit.unit_id: unit for unit in expected.units}
    sequence = expected.ordered_sequences[0]
    ordered = [by_id[unit_id] for unit_id in sequence]
    return ordered + [unit for unit in expected.units if unit.unit_id not in sequence]


def test_fid_score_case_passes_on_the_fixtures_units_and_fails_on_missing_reordered_forbidden(
    instrument: InstrumentLoader,
) -> None:
    gate_fid = instrument("gates.gate_fid")
    chosen = _simple_fid_cases(instrument("fixtures.fid_cases").build_fid_cases())
    assert len(chosen) >= 5, sorted(chosen)

    for fmt, case in sorted(chosen.items()):
        units = _ordered_units(case)
        rendered = "\n".join(unit.text for unit in units)  # type: ignore[attr-defined]
        forbidden = case.expected.forbidden  # type: ignore[attr-defined]
        assert not any(token in rendered for token in forbidden), fmt  # construction control
        missing = "\n".join(unit.text for unit in units[:-1])  # type: ignore[attr-defined]
        reordered = "\n".join(unit.text for unit in reversed(units))  # type: ignore[attr-defined]

        _check_verdict(gate_fid.score_case(case, rendered), case, passed=True)
        _check_verdict(gate_fid.score_case(case, missing), case, passed=False)
        _check_verdict(gate_fid.score_case(case, ""), case, passed=False)
        if case.expected.ordered_sequences:  # type: ignore[attr-defined]
            _check_verdict(gate_fid.score_case(case, reordered), case, passed=False)
        if forbidden:
            poisoned = rendered + "\n" + forbidden[0] + "\n"
            _check_verdict(gate_fid.score_case(case, poisoned), case, passed=False)


@pytest.mark.parametrize(
    "gate", ["gate_time", "gate_red", "gate_snap", "gate_ident", "gate_vis", "gate_cov", "gate_fid"]
)
def test_each_scored_gate_exposes_its_pure_surface(instrument: InstrumentLoader, gate: str) -> None:
    module = instrument(f"gates.{gate}")
    expected = {
        "gate_time": ("score_case",),
        "gate_red": ("score_canary", "score_negative"),
        "gate_snap": ("score_case",),
        "gate_ident": ("score_pair",),
        "gate_vis": ("score_probe",),
        "gate_cov": ("dispose", "score_scenario"),
        "gate_fid": ("score_case",),
    }[gate]

    assert all(callable(getattr(module, name)) for name in expected)
    assert callable(module.evaluate)
