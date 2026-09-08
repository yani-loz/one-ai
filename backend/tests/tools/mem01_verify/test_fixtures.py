"""
Role: Seals FIXTURES_V1 (contract §10) — every battery's minimum size, the four provenance
      fields on every record, criterion ids that exist in the annex, frozen records, reserved
      domains only, the TIME states and RED beyond-cap canaries, the VIS matrix probe counts, and
      a fixtures digest stable across processes (no hash-seed dependence).
Used by: the seal review.
Depends on: tools.mem01_verify.fixtures.* (imported inside each test);
      tests.tools.mem01_verify.reference walkers; the criteria annex through pyyaml.
Key invariants:
  - Record SHAPES beyond the four contract fields are not assumed; the walkers find records by
    those fields wherever a battery nests them.
"""

from __future__ import annotations

import dataclasses
import re
import shutil
import sys
from pathlib import Path

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import BACKEND_ROOT, InstrumentLoader

RESERVED_DOMAINS = ("example.test", "acme.test", "partner.test")
EMAIL_TOKEN = re.compile(r"[\w.+-]+@((?:[\w-]+\.)+[\w-]+)")
CASE_ID = re.compile(
    r"(vis|time|red|ident|fid|idem|snap|cov|thr|attr|ch|nf|lang|ret|emb|erase|qs)-[0-9]{3,}"
)
BATTERIES: dict[str, tuple[str, str, int]] = {
    # name: (module, attribute or callable, minimum records)
    "time": ("fixtures.time_cases", "TIME_CASES", 30),
    "red_positives": ("fixtures.red_cases", "RED_POSITIVES", 100),
    "red_negatives": ("fixtures.red_cases", "RED_NEGATIVES", 100),
    "alias_pairs": ("fixtures.ident_cases", "ALIAS_PAIRS", 20),
    "distinct_pairs": ("fixtures.ident_cases", "DISTINCT_PAIRS", 40),
    "stability_controls": ("fixtures.ident_cases", "STABILITY_CONTROLS", 10),
    "fid": ("fixtures.fid_cases", "build_fid_cases", 70),
    "idem": ("fixtures.idem_scenarios", "IDEM_SCENARIOS", 10),
    "snap": ("fixtures.snap_cases", "SNAP_CASES", 30),
    "cov": ("fixtures.cov_scenarios", "COV_SCENARIOS", 20),
    "vis": ("fixtures.vis_matrix", "build_vis_matrix", 30),
}


def _battery(instrument: InstrumentLoader, name: str) -> tuple[object, int]:
    module_name, attribute, minimum = BATTERIES[name]
    value = getattr(instrument(module_name), attribute)
    return (value() if callable(value) else value), minimum


def _annex_ids(criteria_yaml: dict) -> set[str]:
    return {c["id"] for gate in criteria_yaml["gates"].values() for c in gate["criteria"]}


@pytest.mark.parametrize("name", sorted(BATTERIES))
def test_battery_meets_its_minimum_with_provenance_fields_and_unique_case_ids(
    instrument: InstrumentLoader, criteria_yaml: dict, name: str
) -> None:
    battery, minimum = _battery(instrument, name)

    records = reference.collect_fixture_records(battery)

    assert len(records) >= minimum, (name, len(records))
    case_ids = [record.case_id for record in records]
    assert len(set(case_ids)) == len(case_ids)
    for record in records:
        assert isinstance(record.case_id, str) and CASE_ID.fullmatch(record.case_id), record.case_id
        assert record.criterion_id in _annex_ids(criteria_yaml), record.criterion_id
        assert isinstance(record.origin, str) and record.origin
        assert record.expected is not None
        assert dataclasses.is_dataclass(record)
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.case_id = "mutated"  # type: ignore[misc]


@pytest.mark.parametrize("name", ["vis", "alias_pairs", "distinct_pairs", "stability_controls"])
def test_battery_addresses_use_reserved_domains_only(
    instrument: InstrumentLoader, name: str
) -> None:
    """§10 reserves example/acme/partner.test; §10.4 additionally REQUIRES look-alike and IDN
    domains for the must-remain-distinct pairs, so the IDENT batteries are held to the reserved
    `.test` TLD (RFC 2606) and every other battery to the three domains exactly."""
    battery, _ = _battery(instrument, name)

    domains = {
        match.group(1).lower()
        for value in reference.collect_strings(battery)
        for match in EMAIL_TOKEN.finditer(value)
    }

    assert domains, name  # positive control: the battery does contain addresses
    if name in ("alias_pairs", "distinct_pairs", "stability_controls"):
        assert all(domain.endswith(".test") for domain in domains), domains
    else:
        assert all(domain.endswith(RESERVED_DOMAINS) for domain in domains), domains


def test_vis_matrix_has_enough_positive_negative_and_inheritance_probes(
    instrument: InstrumentLoader,
) -> None:
    matrix, _ = _battery(instrument, "vis")

    records = reference.collect_fixture_records(matrix)
    by_criterion: dict[str, int] = {}
    for record in records:
        by_criterion[record.criterion_id] = by_criterion.get(record.criterion_id, 0) + 1

    assert by_criterion.get("vis.no_forbidden_rows", 0) >= 12
    assert by_criterion.get("vis.no_missing_allowed", 0) >= 12
    assert by_criterion.get("vis.no_wrong_inherited_relations", 0) >= 6
    strings = " ".join(reference.collect_strings(matrix)).lower()
    assert "bcc" in strings and "thread_expansion" in strings and "vector_search" in strings


def test_time_cases_cover_unknown_zone_and_malformed_states_from_the_rfc(
    instrument: InstrumentLoader,
) -> None:
    cases, _ = _battery(instrument, "time")

    strings = reference.collect_strings(cases)
    joined = " ".join(strings)

    assert "unknown_zone" in joined and "malformed" in joined
    assert any("-0000" in value for value in strings)
    assert any(re.search(r"[+-]\d{4}", value) for value in strings if "-0000" not in value)


def test_red_batteries_cover_the_scan_cap_and_the_surface_matrix(
    instrument: InstrumentLoader, criteria_yaml: dict
) -> None:
    red_cases = instrument("fixtures.red_cases")
    positives = list(red_cases.RED_POSITIVES)
    beyond = [case for case in positives if case.placement == "beyond_cap"]
    straddling = [case for case in positives if case.placement == "straddling_cap"]
    inside = [case for case in positives if case not in beyond and case not in straddling]
    surfaces = set(reference.collect_strings(red_cases.RED_SURFACES))

    # §16.10: the numbers, not a label
    assert beyond and straddling and inside
    assert all(case.canary_span[0] > 2_000_000 for case in beyond)
    assert all(case.canary_span[0] < 2_000_000 < case.canary_span[1] for case in straddling)
    assert all(case.canary_span[1] <= 2_000_000 for case in inside)  # positive control
    assert all(case.canary_span[0] < case.canary_span[1] for case in positives)
    assert {surface["name"] for surface in criteria_yaml["red_surfaces"]} <= surfaces


def test_fixtures_digest_is_a_sha256_stable_across_processes(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    digest_module = instrument("fixtures.digest")
    fixtures_dir = BACKEND_ROOT / "tools" / "mem01_verify" / "fixtures"
    in_process = digest_module.fixtures_digest()
    command = [
        sys.executable,
        "-c",
        "from tools.mem01_verify.fixtures.digest import fixtures_digest; print(fixtures_digest())",
    ]
    copy_root = tmp_path / "copy"
    shutil.copytree(
        BACKEND_ROOT / "tools", copy_root / "tools", ignore=shutil.ignore_patterns("__pycache__")
    )

    runs = [
        reference.run_subprocess(
            command, BACKEND_ROOT, reference.clean_child_env({"PYTHONHASHSEED": seed}), 300
        )
        for seed in ("1", "2")
    ]
    copied = reference.run_subprocess(command, copy_root, reference.clean_child_env({}), 300)
    with (copy_root / "tools" / "mem01_verify" / "fixtures" / "stubs_d.py").open(
        "a", encoding="utf-8"
    ) as handle:
        handle.write("\n# oracle mutation: one line of fixture source changed\n")
    mutated = reference.run_subprocess(command, copy_root, reference.clean_child_env({}), 300)

    assert re.fullmatch(r"[0-9a-f]{64}", in_process)
    assert in_process == reference.fixtures_digest_reference(fixtures_dir)  # §16.9, independent
    assert all(run.exit_code == 0 for run in (*runs, copied, mutated)), [
        run.stderr[-500:] for run in (*runs, copied, mutated)
    ]
    assert {run.stdout.strip() for run in runs} == {in_process}
    assert copied.stdout.strip() == in_process  # content-based, not path-based
    assert mutated.stdout.strip() != in_process  # sensitive to one fixture byte


def test_fixture_modules_live_under_the_runner_folder(instrument: InstrumentLoader) -> None:
    module = instrument("fixtures.time_cases")

    assert Path(module.__file__).is_relative_to(
        BACKEND_ROOT / "tools" / "mem01_verify" / "fixtures"
    )
