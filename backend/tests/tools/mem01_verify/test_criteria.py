"""
Role: Seals the criteria annex loader of contract §4.5 / §1.4 — the real draft annex loads with
      every field, the frozen provisional order, `kind` semantics, and every rejection the
      contract enumerates (missing field, unknown gate, duplicate id, zero_denominator other than
      error, ratio minimum < 1, count with a denominator, dangling partner, wrong provisional list).
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.criteria and .exceptions (imported inside each test); pyyaml to
      author mutated copies of the annex (the annex itself is never edited).
Key invariants:
  - Every rejection test first proves the unmutated round-tripped copy loads (the mutation
    harness is under test too).
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from tests.tools.mem01_verify.conftest import GATE_NAMES, InstrumentLoader

CRITERION_FIELDS = {
    "id",
    "gate",
    "set",
    "evidence_basis",
    "split_source",
    "kind",
    "formula",
    "numerator_def",
    "denominator_def",
    "operator",
    "threshold",
    "minimum",
    "zero_denominator",
    "edge_policies",
    "worked_example",
    "partner",
    "stage_available",
    "diagnostic_only",
    "directional",
}
CRITERIA_FILE_FIELDS = {
    "criteria_version",
    "release_state",
    "founder_defaults",
    "provisional_gates",
    "directional_gates",
    "env_allowlist",
    "config_files",
    "scope_policy",
    "qs_surfaces",
    "red_surfaces",
    "chunk_policy",
    "leakage_policy",
    "criteria",
    "by_gate",
}
HOLDOUT_GATES = ("FID", "THR", "IDENT", "ATTR")


def _write_yaml(path: Path, document: dict) -> Path:
    path.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _criterion(document: dict, criterion_id: str) -> dict:
    for gate in document["gates"].values():
        for criterion in gate["criteria"]:
            if criterion["id"] == criterion_id:
                return criterion
    raise KeyError(criterion_id)


def test_real_draft_annex_loads_with_frozen_header_fields(
    instrument: InstrumentLoader, criteria_path: Path, criteria_yaml: dict
) -> None:
    criteria = instrument("criteria")

    loaded = criteria.load_criteria(criteria_path)

    assert loaded.criteria_version == "step1.v1" and loaded.release_state == "draft"
    assert tuple(loaded.provisional_gates) == HOLDOUT_GATES
    assert tuple(loaded.directional_gates) == ()
    assert tuple(loaded.env_allowlist) == tuple(criteria_yaml["env_allowlist"])
    assert tuple(loaded.qs_surfaces) == ("email_body", "email_subject", "attachment_text")
    assert {surface["name"] for surface in criteria_yaml["red_surfaces"]} <= set(
        loaded.red_surfaces
    )
    assert loaded.scope_policy["version"] == "v0"
    assert loaded.leakage_policy["ubiquity_review_trigger"] == 25
    assert loaded.chunk_policy["cap_tokens_email"] == 500
    assert CRITERIA_FILE_FIELDS <= {field.name for field in dataclasses.fields(loaded)}


def test_real_draft_annex_config_artifacts_declare_env_unhashed(
    instrument: InstrumentLoader, criteria_path: Path
) -> None:
    criteria = instrument("criteria")

    loaded = criteria.load_criteria(criteria_path)

    artifacts = {artifact.path: artifact for artifact in loaded.config_files}
    assert artifacts["backend/.env"].hashed is False
    assert artifacts["backend/uv.lock"].hashed is True
    assert all(isinstance(artifact.role, str) and artifact.role for artifact in artifacts.values())


def test_real_draft_annex_criteria_carry_every_field_and_unique_prefixed_ids(
    instrument: InstrumentLoader, criteria_path: Path, criteria_yaml: dict
) -> None:
    criteria = instrument("criteria")
    ids_in_yaml = [c["id"] for gate in criteria_yaml["gates"].values() for c in gate["criteria"]]

    loaded = criteria.load_criteria(criteria_path)

    assert sorted(c.id for c in loaded.criteria) == sorted(ids_in_yaml)
    assert len(ids_in_yaml) == 58 and len(loaded.criteria) == 58
    assert sorted(c.id for c in loaded.by_gate["VIS"]) == [
        "vis.no_forbidden_rows",
        "vis.no_missing_allowed",
        "vis.no_wrong_inherited_relations",
        "vis.route_state_coverage",
    ]
    assert len({c.id for c in loaded.criteria}) == len(loaded.criteria)
    assert set(loaded.by_gate) == set(GATE_NAMES)
    for criterion in loaded.criteria:
        assert CRITERION_FIELDS <= {field.name for field in dataclasses.fields(criterion)}
        assert criterion.id.startswith(criterion.gate.lower() + ".")
        assert criterion.gate == criterion.set
        assert criterion.operator in ("==", "<=", ">=")
        assert criterion.zero_denominator == "error"
        assert isinstance(criterion.edge_policies, tuple) and criterion.edge_policies
        assert isinstance(criterion.partner, tuple)
        assert criterion in loaded.by_gate[criterion.gate]


def test_real_draft_annex_kind_semantics(instrument: InstrumentLoader, criteria_path: Path) -> None:
    criteria = instrument("criteria")

    loaded = criteria.load_criteria(criteria_path)

    by_id = {criterion.id: criterion for criterion in loaded.criteria}
    count = by_id["ident.provisional.c_no_unconfirmed_merge"]
    assert count.kind == "count" and count.denominator_def is None and count.minimum is None
    ratios = [criterion for criterion in loaded.criteria if criterion.kind == "ratio"]
    assert ratios and all(c.minimum is not None and c.minimum >= 1 for c in ratios)
    assert all(isinstance(c.denominator_def, str) and c.denominator_def for c in ratios)


def test_real_draft_annex_holdout_gates_have_provisional_and_validation_entries(
    instrument: InstrumentLoader, criteria_path: Path
) -> None:
    criteria = instrument("criteria")

    loaded = criteria.load_criteria(criteria_path)

    for gate in HOLDOUT_GATES:
        ids = [criterion.id for criterion in loaded.by_gate[gate]]
        assert any(f"{gate.lower()}.provisional" in cid for cid in ids)
        validation = [
            c for c in loaded.by_gate[gate] if c.id.startswith(f"{gate.lower()}.validation")
        ]
        assert validation and all(c.split_source == "validation" for c in validation)


def test_every_gate_has_at_least_one_non_diagnostic_criterion(
    instrument: InstrumentLoader, criteria_path: Path
) -> None:
    criteria = instrument("criteria")

    loaded = criteria.load_criteria(criteria_path)

    for gate in GATE_NAMES:
        assert any(not criterion.diagnostic_only for criterion in loaded.by_gate[gate])


def test_criteria_sha256_is_the_sha256_of_the_file_bytes(
    instrument: InstrumentLoader, criteria_path: Path
) -> None:
    criteria = instrument("criteria")

    assert (
        criteria.criteria_sha256(criteria_path)
        == hashlib.sha256(criteria_path.read_bytes()).hexdigest()
    )


def test_round_tripped_annex_copy_still_loads(
    instrument: InstrumentLoader, criteria_yaml: dict, tmp_path: Path
) -> None:
    criteria = instrument("criteria")
    copy_path = _write_yaml(tmp_path / "criteria.step1.v1.yaml", copy.deepcopy(criteria_yaml))

    loaded = criteria.load_criteria(copy_path)

    assert tuple(loaded.provisional_gates) == HOLDOUT_GATES


def _rejections() -> dict[str, Callable[[dict], None]]:
    def mutate_criterion(criterion_id: str, **changes: object) -> Callable[[dict], None]:
        def apply(document: dict) -> None:
            _criterion(document, criterion_id).update(changes)

        return apply

    def drop_field(criterion_id: str, field: str) -> Callable[[dict], None]:
        def apply(document: dict) -> None:
            _criterion(document, criterion_id).pop(field)

        return apply

    def duplicate_id(document: dict) -> None:
        _criterion(document, "qs.echo_incidence")["id"] = "qs.no_content_loss"

    def unknown_gate(document: dict) -> None:
        document["gates"]["FOO"] = copy.deepcopy(document["gates"]["SNAP"])
        for criterion in document["gates"]["FOO"]["criteria"]:
            criterion["id"] = criterion["id"].replace("snap.", "foo.")
            criterion["set"] = "FOO"

    def drop_fid_validation(document: dict) -> None:
        fid = document["gates"]["FID"]
        fid["criteria"] = [c for c in fid["criteria"] if c["id"] != "fid.validation"]
        for criterion in fid["criteria"]:
            criterion["partner"] = [p for p in criterion["partner"] if p != "fid.validation"]

    def all_snap_diagnostic(document: dict) -> None:
        for criterion in document["gates"]["SNAP"]["criteria"]:
            criterion["diagnostic_only"] = True

    def set_top(key: str, value: object) -> Callable[[dict], None]:
        return lambda document: document.__setitem__(key, value)

    return {
        "missing_minimum": drop_field("qs.no_content_loss", "minimum"),
        "missing_formula": drop_field("snap.replay_hash_equality", "formula"),
        "missing_edge_policies": drop_field("time.fixtures", "edge_policies"),
        "unknown_gate": unknown_gate,
        "duplicate_id": duplicate_id,
        "provisional_wrong_order": set_top("provisional_gates", ["THR", "FID", "IDENT", "ATTR"]),
        "provisional_missing_one": set_top("provisional_gates", ["FID", "THR", "IDENT"]),
        "zero_denominator_vacuous_pass": mutate_criterion(
            "ident.provisional.c_normalization_key", zero_denominator="vacuous_pass"
        ),
        "ratio_minimum_zero": mutate_criterion("vis.no_forbidden_rows", minimum=0),
        "count_with_denominator": mutate_criterion(
            "ident.provisional.c_no_unconfirmed_merge", denominator_def="all merges", minimum=1
        ),
        "dangling_partner": mutate_criterion(
            "snap.source_span_mappings", partner=["snap.does_not_exist"]
        ),
        "operator_less_than": mutate_criterion("red.no_over_redaction", operator="<"),
        "stage_available_unknown": mutate_criterion("cov.fixtures", stage_available="Z"),
        "kind_unknown": mutate_criterion("emb.vectors_valid", kind="weight"),
        "id_without_gate_prefix": mutate_criterion("lang.no_invalid_states", id="invalid_states"),
        "gate_without_mandatory_criterion": all_snap_diagnostic,
        "holdout_without_validation_entry": drop_fid_validation,
        "missing_gate_block": lambda document: document["gates"].pop("EMB"),
    }


@pytest.mark.parametrize("name", sorted(_rejections()))
def test_load_criteria_rejects_each_violation(
    instrument: InstrumentLoader, criteria_yaml: dict, tmp_path: Path, name: str
) -> None:
    criteria = instrument("criteria")
    exceptions = instrument("exceptions")
    document = copy.deepcopy(criteria_yaml)
    criteria.load_criteria(_write_yaml(tmp_path / "control.yaml", copy.deepcopy(document)))
    _rejections()[name](document)

    with pytest.raises(exceptions.CriteriaError):
        criteria.load_criteria(_write_yaml(tmp_path / "mutated.yaml", document))


def test_criteria_error_is_a_mem01_error(instrument: InstrumentLoader) -> None:
    exceptions = instrument("exceptions")

    assert issubclass(exceptions.CriteriaError, exceptions.Mem01Error)


def test_loaded_criteria_are_frozen(instrument: InstrumentLoader, criteria_path: Path) -> None:
    criteria = instrument("criteria")
    loaded = criteria.load_criteria(criteria_path)

    with pytest.raises(dataclasses.FrozenInstanceError):
        loaded.criteria[0].threshold = 1.0  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        loaded.release_state = "frozen"  # type: ignore[misc]
