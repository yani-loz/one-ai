"""
Role: Proofs of the oracle's own release builders and seeding row builders, runnable WITHOUT the
      instrument (test-env brief §5b) — the synthetic release manifest (per-split records, hidden
      paths and their decoys, re-manifested visible files), the frozen-release builder derived
      from a draft (every regular file named, the hidden split partitioned, tampered hashes),
      and the ORM row builders the probe corpus is seeded with.
Used by: the seal review (a red proof here means the oracle, not the instrument, is wrong).
Depends on: tests.tools.mem01_verify.synthetic_release, .frozen_release, .seeding_rows (the
      latter imports app.* ORM models only to construct rows); stdlib.
Key invariants:
  - No test here imports tools.mem01_verify; every expectation is computed with hashlib/json.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def test_synthetic_release_manifest_names_every_file_and_resolves_hidden_paths(
    tmp_path: Path,
) -> None:
    from tests.tools.mem01_verify import synthetic_release

    built = synthetic_release.build_release(
        tmp_path,
        state="frozen",
        runner_sha256="ab" * 32,
        hidden_records={"QS": ["qs-0002", "qs-0001"]},
        optimization_records={"NF": ["nf-0001"]},
        extra_unmanifested={"extra.txt": b"x"},
    )
    manifest = built.manifest()

    assert (
        manifest["criteria_sha256"]
        == hashlib.sha256((built.path / "criteria.step1.v1.yaml").read_bytes()).hexdigest()
    )
    for relative, entry in manifest["files"].items():
        if entry["visibility"] == "visible":
            assert (
                entry["sha256"] == hashlib.sha256((built.path / relative).read_bytes()).hexdigest()
            )
    visible = {r for r, e in manifest["files"].items() if e["visibility"] == "visible"}
    assert visible == {
        "criteria.step1.v1.yaml",
        "PROTOCOL.v1.md",
        "schemas/record_core.schema.json",
        "data/optimization/NF/part0.jsonl",
    }
    hidden_entry = manifest["files"]["hidden/test/QS/part0.jsonl"]
    hidden_file = built.hidden_file("test", "QS")
    assert hidden_file == built.hidden_root / "releases" / built.name / "test" / "QS" / (
        "part0.jsonl"
    )
    assert hidden_entry["visibility"] == "hidden" and hidden_entry["records"] == 2
    assert hidden_entry["sha256"] == hashlib.sha256(hidden_file.read_bytes()).hexdigest()
    assert [
        json.loads(line)["gold_id"] for line in hidden_file.read_text("utf-8").splitlines()
    ] == ["qs-0002", "qs-0001"]
    decoy = built.decoy_file("test", "QS")
    assert decoy.is_file() and decoy.read_bytes() != hidden_file.read_bytes()
    assert hashlib.sha256(decoy.read_bytes()).hexdigest() != hidden_entry["sha256"]
    assert manifest["records"]["QS"] == {
        "optimization": [],
        "test": ["qs-0001", "qs-0002"],
        "validation": [],
    }
    assert manifest["records"]["NF"] == {"optimization": ["nf-0001"], "test": [], "validation": []}
    assert manifest["records"]["CH"] == synthetic_release.empty_records()
    assert manifest["sets"]["QS"]["expected"] == 2 and manifest["sets"]["NF"]["expected"] == 1
    assert manifest["budget_ledger"] == "hidden_budget.jsonl"
    assert (built.path / "extra.txt").is_file() and "extra.txt" not in manifest["files"]


def test_synthetic_release_write_visible_file_re_manifests_the_entry(tmp_path: Path) -> None:
    from tests.tools.mem01_verify import synthetic_release

    built = synthetic_release.build_release(
        tmp_path, runner_sha256="ab" * 32, optimization_records={"QS": ["qs-0001"]}
    )
    payload = b'{"gold_id": "qs-0001"}\n{"gold_id": "qs-0001"}\n'

    built.write_visible_file("data/optimization/QS/part0.jsonl", payload)

    entry = built.manifest()["files"]["data/optimization/QS/part0.jsonl"]
    assert entry == {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "records": 2,
        "visibility": "visible",
    }
    assert (built.path / "data" / "optimization" / "QS" / "part0.jsonl").read_bytes() == payload


def test_frozen_release_builder_names_every_regular_file_and_partitions_the_hidden_split(
    tmp_path: Path,
) -> None:
    from tests.tools.mem01_verify import frozen_release, synthetic_release

    draft = synthetic_release.build_release(tmp_path / "draft", runner_sha256="ab" * 32)
    (draft.path / "census").mkdir()
    (draft.path / "census" / "census.json").write_bytes(b"{}")

    built = frozen_release.build_frozen_release(
        draft.path, tmp_path / "frozen", runner_sha256="cd" * 32, hidden_split="validation"
    )
    manifest = built.manifest()

    assert manifest["release_state"] == "frozen" and manifest["runner_sha256"] == "cd" * 32
    assert built.ledger_path.read_bytes() == b"" and built.audit_path.read_bytes() == b""
    regular = {
        p.relative_to(built.path).as_posix()
        for p in built.path.rglob("*")
        if p.is_file()
        and p.name not in ("dataset.manifest.json", "audit.jsonl")
        and not p.relative_to(built.path).as_posix().startswith("reports/")
    }
    visible = {r for r, e in manifest["files"].items() if e["visibility"] == "visible"}
    assert visible == regular and "census/census.json" in visible
    assert "data/optimization/QS/part0.jsonl" in visible
    for relative in visible:
        entry = manifest["files"][relative]
        payload = (built.path / relative).read_bytes()
        assert entry["sha256"] == hashlib.sha256(payload).hexdigest()
        assert entry["bytes"] == len(payload)
        assert entry["records"] == (payload.count(b"\n") if relative.endswith(".jsonl") else 0)
    for set_name in frozen_release.H_SETS:
        hidden_ids, visible_ids = built.gold_ids[set_name], built.optimization_ids[set_name]
        assert set(hidden_ids).isdisjoint(visible_ids)
        hidden = built.hidden_file(set_name)
        assert hidden == (
            built.hidden_root / "releases" / built.name / "validation" / set_name / "part0.jsonl"
        )
        assert [
            json.loads(line)["gold_id"] for line in hidden.read_text("utf-8").splitlines()
        ] == list(hidden_ids)
        visible_file = built.path / "data" / "optimization" / set_name / "part0.jsonl"
        assert [
            json.loads(line)["gold_id"] for line in visible_file.read_text("utf-8").splitlines()
        ] == list(visible_ids)
        assert manifest["records"][set_name] == {
            "optimization": sorted(visible_ids),
            "test": [],
            "validation": sorted(hidden_ids),
        }
        assert manifest["sets"][set_name]["expected"] == len(hidden_ids) + len(visible_ids)
        real = manifest["files"][f"hidden/validation/{set_name}/part0.jsonl"]
        assert real["sha256"] == hashlib.sha256(hidden.read_bytes()).hexdigest()
        bogus = manifest["files"][f"hidden/test/{set_name}/part0.jsonl"]
        assert bogus["sha256"] == frozen_release.BOGUS_SHA256
        assert not (built.hidden_root / "releases" / built.name / "test").exists()
        decoy = built.hidden_root / "releases" / built.name / "hidden" / "validation" / set_name
        assert (decoy / "part0.jsonl").is_file()
        assert (decoy / "part0.jsonl").read_bytes() != hidden.read_bytes()
    assert manifest["records"]["CH"] == synthetic_release.empty_records()
    assert manifest["test_groups_provenance"].keys() == manifest["sets"].keys()
    assert built.lock_sha256() == hashlib.sha256(built.manifest_path.read_bytes()).hexdigest()
    before = built.lock_sha256()
    built.write_manifest({**manifest, "cut_at": "2026-09-07T00:00:00+00:00"})
    assert built.lock_sha256() != before


def test_frozen_release_tamper_hidden_flips_only_the_selected_splits_hashes(
    tmp_path: Path,
) -> None:
    from tests.tools.mem01_verify import frozen_release, synthetic_release

    draft = synthetic_release.build_release(tmp_path / "draft", runner_sha256="ab" * 32)
    clean = frozen_release.build_frozen_release(
        draft.path, tmp_path / "clean", runner_sha256="cd" * 32, hidden_split="test"
    )
    tampered = frozen_release.build_frozen_release(
        draft.path,
        tmp_path / "tampered",
        runner_sha256="cd" * 32,
        hidden_split="test",
        tamper_hidden=True,
    )

    clean_files, tampered_files = clean.manifest()["files"], tampered.manifest()["files"]

    for set_name in frozen_release.H_SETS:
        relative = f"hidden/test/{set_name}/part0.jsonl"
        real = hashlib.sha256(tampered.hidden_file(set_name).read_bytes()).hexdigest()
        assert clean_files[relative]["sha256"] == real
        assert tampered_files[relative]["sha256"] != real
        assert tampered_files[relative]["bytes"] == clean_files[relative]["bytes"]
        other = f"hidden/validation/{set_name}/part0.jsonl"
        assert tampered_files[other] == clean_files[other]
    visible = {r for r, e in clean_files.items() if e["visibility"] == "visible"}
    assert all(tampered_files[r]["sha256"] == clean_files[r]["sha256"] for r in visible)


def test_seeding_row_builders_construct_orm_rows_with_the_expected_columns() -> None:
    from uuid import uuid4

    from tests.tools.mem01_verify import seeding_rows

    org_id, connection_id, email_id, attachment_id, person_id = (uuid4() for _ in range(5))

    connection = seeding_rows._connection(org_id, connection_id, "mailbox@acme.test")
    email = seeding_rows._email(
        org_id, connection_id, email_id, message_id="m@acme.test", body_text="b"
    )
    recipient = seeding_rows._recipient(
        org_id, connection_id, email_id, "bcc", "blind@partner.test", person_id
    )
    attachment = seeding_rows._attachment(
        org_id, connection_id, email_id, attachment_id, extraction_status="pending"
    )
    grant = seeding_rows._grant(org_id, person_id, email_id, connection_id)
    person = seeding_rows._person(org_id, person_id, "OraclePersona Smoke")
    person_email = seeding_rows._person_email(org_id, person_id, "smoke@acme.test")

    assert (connection.id, connection.org_id) == (connection_id, org_id)
    assert (email.id, email.org_id, email.connection_id) == (email_id, org_id, connection_id)
    assert email.message_id == "m@acme.test" and email.body_text == "b"
    assert email.parse_status == "parsed" and email.language is None and email.headers == {}
    assert email.visibility_scope == "restricted" and email.container_id == connection_id
    assert (recipient.kind, recipient.address, recipient.person_id) == (
        "bcc",
        "blind@partner.test",
        person_id,
    )
    assert (attachment.id, attachment.email_id, attachment.org_id) == (
        attachment_id,
        email_id,
        org_id,
    )
    assert attachment.extraction_status == "pending" and attachment.is_inline is False
    assert (grant.person_id, grant.object_id, grant.object_type) == (
        person_id,
        email_id,
        "email_message",
    )
    assert grant.provenance == "recipient" and grant.revoked_at is None
    assert (person.id, person.display_name, person.is_internal) == (
        person_id,
        "OraclePersona Smoke",
        True,
    )
    assert (person_email.person_id, person_email.email) == (person_id, "smoke@acme.test")
