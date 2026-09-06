"""
Role: Executable seal for the eval cache's identity basis (ledger M7). A cached answer may only
      be reused for a run that would genuinely have produced it, so every dimension that
      changes answers must change the hash — person, org, corpus, reader endpoint, tools,
      prompt, params, and the tool/runner SOURCE.
Used by: pytest (tests/ask/services) and scripts/ask_loop/seal_check via the ledger.
Depends on: scripts.ask_loop.run_eval._config_hash, app.ask.tools.shared_core,
      app.ask.services.agent_runner. No database and no network.
Key invariants:
  - Each dimension is asserted SEPARATELY. A single "different config hashes differently" test
    would pass while a specific dimension silently dropped out of the basis — which is exactly
    how a permission probe was once served the unbound run's cached answers.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.ask.services.agent_runner import AskAgentRunner
from app.ask.tools.shared_core import build_shared_core_registry
from scripts.ask_loop.run_eval import _config_hash

_ORG = "11111111-1111-1111-1111-111111111111"
_PERSON = "22222222-2222-2222-2222-222222222222"


def _runner() -> AskAgentRunner:
    """A runner with fixed inputs, so only the dimension under test varies."""
    return AskAgentRunner(
        client=None,  # never called: _config_hash only reads configuration off the runner
        registry=build_shared_core_registry(),
        today=date(2026, 1, 1),
    )


def _hash(**overrides: object) -> str:
    """The config hash with one dimension overridden."""
    args: dict[str, object] = {
        "registry": build_shared_core_registry(),
        "runner": _runner(),
        "model": "test-model",
        "model_params": {"temperature": 0},
        "person": _PERSON,
        "org": _ORG,
    }
    args.update(overrides)
    return _config_hash(
        args["registry"],  # type: ignore[arg-type]
        args["runner"],  # type: ignore[arg-type]
        args["model"],  # type: ignore[arg-type]
        args["model_params"],  # type: ignore[arg-type]
        person=args["person"],  # type: ignore[arg-type]
        org=args["org"],  # type: ignore[arg-type]
    )


def test_identical_configuration_hashes_identically() -> None:
    # Without this the other assertions could pass trivially.
    assert _hash() == _hash()


@pytest.mark.parametrize(
    ("dimension", "override"),
    [
        # A person-bound run and an unbound probe see DIFFERENT data — sharing a cache dir
        # meant the permission probe measured nothing.
        ("person", {"person": None}),
        ("other person", {"person": "33333333-3333-3333-3333-333333333333"}),
        ("org", {"org": "44444444-4444-4444-4444-444444444444"}),
        ("model", {"model": "another-model"}),
        ("model params", {"model_params": {"temperature": 0.7}}),
    ],
)
def test_each_answer_changing_dimension_changes_the_hash(
    dimension: str, override: dict[str, object]
) -> None:
    assert _hash(**override) != _hash(), f"{dimension} is not in the cache basis"


@pytest.mark.parametrize(
    ("dimension", "attribute", "value"),
    [
        # Two runs against different DATABASES are different experiments even when every other
        # input matches; nothing else in the basis would notice.
        ("corpus", "postgres_db", "another_database"),
        ("corpus host", "postgres_host", "another-host"),
        # …and neither are two runs against different reader endpoints. Asserted SEPARATELY:
        # a test that names both in its title but varies only the database leaves the endpoint
        # dimension free to drop out of the basis unnoticed.
        ("reader endpoint", "ask_reader_base_url", "https://another.endpoint/v1"),
    ],
)
def test_where_the_answers_came_from_is_in_the_basis(
    monkeypatch: pytest.MonkeyPatch, dimension: str, attribute: str, value: str
) -> None:
    baseline = _hash()

    from app.core import config

    monkeypatch.setattr(config.get_settings(), attribute, value)

    assert _hash() != baseline, f"{dimension} is not in the cache basis"
