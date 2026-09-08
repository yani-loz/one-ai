"""
Role: Seals the Stage A gate outcomes of contract §11 on the synthetic 1,000-email probe corpus
      through a full tuning run — every absent-component gate `incomplete`, LANG `incomplete`
      with its corpus FAIL visible, COV FAIL under the §4.6 scope policy with the inline images
      excluded, TIME FAIL on the naive-date fixtures, IDENT FAIL on unmerged alias pairs, RED FAIL
      on the beyond-cap canaries, SNAP PASS, FID decided on fixtures, VIS/IDEM partly evaluated
      with their incomplete cells named — plus the criteria-entry invariants (pending validation
      entries, provisional acceptance state, denominators at or above the annex minimums).
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: conftest.baseline_pair (the session's full runs), tests.tools.mem01_verify.reference
      (block extraction), the criteria annex through pyyaml.
Key invariants:
  - Expected numbers (1040 language items, 1055 required logical items, 15 not-ready inputs,
    5 exclusions, 2040 snapshot artifacts) are derived from seeding.py's spec and §4.6/§5.1.
"""

from __future__ import annotations

import json

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import SESSION_LOOP, BaselinePairFactory, ProbeCorpusFactory

ABSENT_COMPONENT_GATES = ("QS", "CH", "NF", "ERASE", "RET", "ATTR", "EMB")
# ERASE (`erase.no_dangling_references`) and NF (`nf.dedup_groups_correct`) carry corpus-scanned
# criteria an evaluator may score today; only the gate-level `incomplete` of §11 is sealed there.
STRICTLY_UNEVALUABLE_GATES = ("QS", "CH", "RET", "ATTR", "EMB")
EXPECTED_STATUS = {
    "LANG": "incomplete",
    "IDEM": "incomplete",
    "VIS": "incomplete",
    "THR": "incomplete",
    "COV": "FAIL",
    "TIME": "FAIL",
    "IDENT": "FAIL",
    "RED": "FAIL",
    "SNAP": "PASS",
}


async def _block(baseline_pair: BaselinePairFactory) -> dict:
    pair = await baseline_pair()
    assert pair.before.exit_code == 2, pair.before.stderr[-2000:]
    return reference.extract_machine_block(pair.before.stdout)


def _entry(block: dict, criterion_id: str) -> dict:
    matches = [entry for entry in block["criteria"] if entry["id"] == criterion_id]
    assert len(matches) == 1, criterion_id
    return matches[0]


def _numbers(value: object) -> set[float]:
    found: set[float] = set()
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, bool):
            continue
        if isinstance(current, int | float):
            found.add(current)
        elif isinstance(current, dict):
            stack.extend(current.values())
        elif isinstance(current, list | tuple):
            stack.extend(current)
    return found


@SESSION_LOOP
@pytest.mark.parametrize("gate", ABSENT_COMPONENT_GATES)
async def test_absent_component_gates_print_incomplete_never_pass(
    baseline_pair: BaselinePairFactory, gate: str
) -> None:
    block = await _block(baseline_pair)

    assert block["gates"][gate]["status"] == "incomplete"
    assert block["gates"][gate]["reason"]
    statuses = {_entry(block, cid)["status"] for cid in block["gates"][gate]["criteria"]}
    assert "ERROR" not in statuses and "incomplete" in statuses
    if gate in STRICTLY_UNEVALUABLE_GATES:
        assert statuses <= {"incomplete", "pending", "N/A"}


@SESSION_LOOP
@pytest.mark.parametrize("gate", sorted(EXPECTED_STATUS))
async def test_evaluable_gates_reach_the_section_11_status(
    baseline_pair: BaselinePairFactory, gate: str
) -> None:
    block = await _block(baseline_pair)

    assert block["gates"][gate]["status"] == EXPECTED_STATUS[gate], block["gates"][gate]


@SESSION_LOOP
async def test_fid_is_decided_in_full_on_fixtures(baseline_pair: BaselinePairFactory) -> None:
    block = await _block(baseline_pair)

    assert block["gates"]["FID"]["status"] in ("PASS", "FAIL")
    provisional = _entry(block, "fid.provisional")
    assert provisional["status"] in ("PASS", "FAIL") and provisional["denominator"] >= 70
    assert _entry(block, "fid.validation")["status"] == "pending"


@SESSION_LOOP
async def test_lang_corpus_criterion_fails_visibly_while_the_gate_is_incomplete(
    baseline_pair: BaselinePairFactory, probe_corpus: ProbeCorpusFactory
) -> None:
    corpus = await probe_corpus()
    block = await _block(baseline_pair)
    expected_items = corpus.big.email_count + corpus.big.attachments_with_text

    invalid_states = _entry(block, "lang.no_invalid_states")

    assert invalid_states["status"] == "FAIL"
    assert invalid_states["denominator"] == expected_items
    assert invalid_states["numerator"] == expected_items
    assert _entry(block, "lang.accuracy_overall")["status"] == "incomplete"


@SESSION_LOOP
async def test_cov_fails_on_not_ready_inputs_and_excludes_only_by_property(
    baseline_pair: BaselinePairFactory, probe_corpus: ProbeCorpusFactory
) -> None:
    corpus = await probe_corpus()
    block = await _block(baseline_pair)
    big = corpus.big
    inline_images = 5
    required_logical = big.email_count + big.attachment_count - inline_images

    accounted = _entry(block, "cov.physical_inputs_accounted")
    delivered = _entry(block, "cov.required_logical_delivered")

    assert accounted["status"] == "PASS"
    assert accounted["denominator"] == big.email_count + big.attachment_count
    assert delivered["status"] == "FAIL"
    assert delivered["denominator"] == required_logical
    assert delivered["numerator"] == big.not_ready_attachment_count
    assert len(block["exclusions"]) == inline_images
    assert all({"id", "reason", "policy_ref"} <= set(item) for item in block["exclusions"])
    assert not any(marker in json.dumps(block["exclusions"]) for marker in big.personal_markers)


@SESSION_LOOP
async def test_time_fixture_criterion_fails_on_the_naive_date_pin(
    baseline_pair: BaselinePairFactory,
) -> None:
    block = await _block(baseline_pair)

    fixtures = _entry(block, "time.fixtures")
    comparison = _entry(block, "time.header_comparison")

    assert fixtures["status"] == "FAIL" and fixtures["denominator"] >= 30
    assert fixtures["numerator"] >= 1
    assert comparison["status"] in ("PASS", "FAIL") and comparison["denominator"] >= 100


@SESSION_LOOP
async def test_ident_fails_because_confirmed_alias_pairs_stay_unmerged(
    baseline_pair: BaselinePairFactory,
) -> None:
    block = await _block(baseline_pair)

    alias = _entry(block, "ident.provisional.alias_resolution")
    merges = _entry(block, "ident.provisional.c_no_unconfirmed_merge")

    assert alias["status"] == "FAIL" and alias["denominator"] >= 20
    assert alias["numerator"] == alias["denominator"]
    assert merges["kind"] == "count" and merges["status"] == "PASS"
    assert merges["denominator"] is None and merges["numerator"] == 0
    assert _entry(block, "ident.validation")["status"] == "pending"


@SESSION_LOOP
async def test_red_fails_on_canaries_beyond_the_scan_cap(
    baseline_pair: BaselinePairFactory,
) -> None:
    block = await _block(baseline_pair)

    under = _entry(block, "red.no_under_redaction")
    over = _entry(block, "red.no_over_redaction")

    assert under["status"] == "FAIL" and under["denominator"] >= 100 and under["numerator"] >= 1
    assert over["denominator"] >= 100


@SESSION_LOOP
async def test_snap_passes_over_every_artifact_of_the_org(
    baseline_pair: BaselinePairFactory, probe_corpus: ProbeCorpusFactory
) -> None:
    corpus = await probe_corpus()
    block = await _block(baseline_pair)

    replay = _entry(block, "snap.replay_hash_equality")
    mappings = _entry(block, "snap.source_span_mappings")

    assert replay["status"] == "PASS" and replay["numerator"] == 0
    assert replay["denominator"] == corpus.big.text_artifact_count
    assert mappings["status"] == "PASS" and mappings["denominator"] >= 30


@SESSION_LOOP
async def test_vis_cells_are_evaluated_with_positive_and_negative_probes_and_name_the_rest(
    baseline_pair: BaselinePairFactory,
) -> None:
    block = await _block(baseline_pair)

    forbidden = _entry(block, "vis.no_forbidden_rows")
    allowed = _entry(block, "vis.no_missing_allowed")
    inherited = _entry(block, "vis.no_wrong_inherited_relations")
    coverage = _entry(block, "vis.route_state_coverage")
    narrative = (block["gates"]["VIS"]["reason"] + json.dumps(block["diagnostics"]["VIS"])).lower()

    assert forbidden["status"] == "PASS" and forbidden["denominator"] >= 12
    assert allowed["status"] == "PASS" and allowed["denominator"] >= 12
    assert inherited["status"] == "PASS" and inherited["denominator"] >= 6
    assert "thread" in narrative and "vector" in narrative
    assert coverage["status"] == "incomplete"  # stage D routes have no plane yet
    assert block["gates"]["VIS"]["status"] == "incomplete"


@SESSION_LOOP
async def test_idem_replay_criteria_pass_and_backfill_is_incomplete(
    baseline_pair: BaselinePairFactory,
) -> None:
    block = await _block(baseline_pair)

    replay = _entry(block, "idem.replay_no_change")
    once = _entry(block, "idem.exactly_once_committed")

    assert replay["status"] == "PASS" and replay["denominator"] >= 10
    assert once["status"] == "PASS" and once["denominator"] >= 10
    assert _entry(block, "idem.backfill_one_new_version")["status"] == "incomplete"


@SESSION_LOOP
async def test_criteria_entries_respect_annex_minimums_and_loop_acceptance_state(
    baseline_pair: BaselinePairFactory, criteria_yaml: dict
) -> None:
    block = await _block(baseline_pair)
    annex = {c["id"]: c for gate in criteria_yaml["gates"].values() for c in gate["criteria"]}

    assert {entry["id"] for entry in block["criteria"]} == set(annex)
    for entry in block["criteria"]:
        spec = annex[entry["id"]]
        assert entry["gate"] == spec["set"] and entry["kind"] == spec["kind"]
        assert entry["acceptance_state"] == "provisional"
        if spec["split_source"] == "validation":
            assert entry["status"] == "pending"
        if entry["status"] in ("PASS", "FAIL") and entry["kind"] == "ratio":
            # a decided ratio never sits below its minimum (R2: that would have been ERROR)
            assert entry["denominator"] >= spec["minimum"] > 0, entry["id"]
    assert block["provisional_gates"] == ["FID", "THR", "IDENT", "ATTR"]
    assert block["directional_gates"] == []


@SESSION_LOOP
async def test_diagnostics_count_quote_markers_and_skipped_nondocuments(
    baseline_pair: BaselinePairFactory,
) -> None:
    block = await _block(baseline_pair)

    assert 20 in _numbers(block["diagnostics"]["QS"])  # emails seeded with '> quoted' lines
    assert 5 in _numbers(block["diagnostics"]["NF"])  # skipped_nondocument image/png parts
    assert "image/png" in json.dumps(block["diagnostics"]["NF"])
