"""
Role: Seals fix-registry row A1(a) — the environment the instrument hands to its Alembic child
      process is built ONLY from the annex `env_allowlist` names present in the parent (values
      passed through unchanged) plus the explicit overrides `POSTGRES_DB=<probe>`,
      `POSTGRES_HOST` and `POSTGRES_PORT`; nothing else reaches the child, and the parent's own
      connection settings never win over the overrides.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.probe_db (`child_environment`, imported inside each test).
Key invariants:
  - The parent mapping is authored by hand: it carries names that are NOT allowlisted
    (`PGSSLMODE_EVIL`, `SECRET_X`, `PYTHONPATH`, `MEM01_SENTINEL_SECRET`) and the corpus
    database name in `POSTGRES_DB`,
    so a pass-through of the parent shows up as a leak or as the wrong target database.
  - The function is pure: no test reads or writes `os.environ`.
"""

from __future__ import annotations

from tests.tools.mem01_verify.conftest import InstrumentLoader

PROBE = "mem01_probe_20260906t120000z_0a1b2c3d"
HOST, PORT = "localhost", "5432"
OVERRIDE_NAMES = frozenset({"POSTGRES_DB", "POSTGRES_HOST", "POSTGRES_PORT"})
ALLOWLIST = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "APP_ENV",
    "MEM01_GOLD_ROOT",
    "PATH",
    "ABSENT_IN_PARENT",
)
PARENT = {
    "POSTGRES_HOST": "corpus.internal",
    "POSTGRES_PORT": "55432",
    "POSTGRES_DB": "oneai",  # the configured corpus database: must never reach the child
    "POSTGRES_USER": "oneai",
    "POSTGRES_PASSWORD": "pass word = with spaces and ünïcode",
    "APP_ENV": "test",
    "MEM01_GOLD_ROOT": "C:\\gold root\\with spaces",
    "PATH": "/usr/bin:/bin",
    "PGSSLMODE_EVIL": "disable",
    "SECRET_X": "hunter2",
    "PYTHONPATH": "/somewhere/else",
    "MEM01_SENTINEL_SECRET": "sentinel-value-2f9c",
}
PRESENT_ALLOWLISTED = {
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "APP_ENV",
    "MEM01_GOLD_ROOT",
    "PATH",
}


def _child(instrument: InstrumentLoader, **overrides: object) -> dict[str, str]:
    arguments: dict[str, object] = {
        "parent": PARENT,
        "allowlist": ALLOWLIST,
        "host": HOST,
        "port": PORT,
    }
    arguments.update(overrides)
    return instrument("probe_db").child_environment(PROBE, **arguments)


def test_child_environment_keys_are_the_present_allowlist_names_plus_the_three_overrides(
    instrument: InstrumentLoader,
) -> None:
    child = _child(instrument)

    assert isinstance(child, dict)
    assert set(child) <= set(ALLOWLIST) | OVERRIDE_NAMES
    assert set(child) == PRESENT_ALLOWLISTED | OVERRIDE_NAMES
    assert "ABSENT_IN_PARENT" not in child  # allowlisted but absent in the parent: absent


def test_child_environment_never_carries_a_parent_name_outside_the_allowlist(
    instrument: InstrumentLoader,
) -> None:
    child = _child(instrument)

    assert "PGSSLMODE_EVIL" not in child and "SECRET_X" not in child
    assert "PYTHONPATH" not in child
    assert "hunter2" not in child.values() and "disable" not in child.values()  # no renamed leak
    assert child["APP_ENV"] == "test"  # positive control: an allowlisted name is present


def test_child_environment_overrides_target_the_probe_never_the_parents_corpus_database(
    instrument: InstrumentLoader,
) -> None:
    child = _child(instrument)
    elsewhere = _child(instrument, host="db.example.test", port="6543")

    assert child["POSTGRES_DB"] == PROBE  # the parent's corpus name never becomes the target
    assert child["POSTGRES_HOST"] == HOST and child["POSTGRES_PORT"] == PORT
    assert "MEM01_SENTINEL_SECRET" not in child
    assert "sentinel-value-2f9c" not in child.values()
    assert elsewhere["POSTGRES_HOST"] == "db.example.test"
    assert elsewhere["POSTGRES_PORT"] == "6543" and elsewhere["POSTGRES_DB"] == PROBE


def test_child_environment_passes_allowlisted_values_through_unchanged(
    instrument: InstrumentLoader,
) -> None:
    child = _child(instrument)

    for name in ("POSTGRES_USER", "POSTGRES_PASSWORD", "APP_ENV", "MEM01_GOLD_ROOT", "PATH"):
        assert child[name] == PARENT[name], name


def test_child_environment_with_an_empty_allowlist_is_exactly_the_three_overrides(
    instrument: InstrumentLoader,
) -> None:
    child = _child(instrument, allowlist=())

    assert child == {"POSTGRES_DB": PROBE, "POSTGRES_HOST": HOST, "POSTGRES_PORT": PORT}


def test_child_environment_leaves_the_parent_mapping_untouched(
    instrument: InstrumentLoader,
) -> None:
    parent = dict(PARENT)

    child = _child(instrument, parent=parent)

    assert parent == PARENT
    assert child is not parent and child != parent
