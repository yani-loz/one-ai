"""
Role: Unit tests for the Ask intent router — pins kit/registry reconciliation (every kit
      resolves FULLY against the live shared-core registry; a phantom tool name raises, N12)
      and the last-mention classification rule on both channels (N13). Pure Python — no DB,
      no network (fake chat client).
Used by: pytest (tests/ask/services).
Depends on: app.ask.services.router, app.ask.tools.registry/shared_core.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ask.exceptions import UnknownToolError
from app.ask.services.router import INTENT_CLASSES, classify_question, registry_for_class
from app.ask.tools.registry import ToolRegistry, ToolSpec
from app.ask.tools.shared_core import build_shared_core_registry


class _FakeChatClient:
    """Returns one canned assistant message — enough to drive classify_question."""

    def __init__(self, message: dict[str, Any]) -> None:
        self._message = message

    async def chat(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._message


class _ExplodingChatClient:
    """Raises on every call — the router must fall back to the generalist (None)."""

    async def chat(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("router model down")


class _BillingChatClient:
    """Charges a fixed cost per call into whatever sink the caller supplies."""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        sink = kwargs.get("usage_sink")
        if sink is not None:
            sink["prompt_tokens"] = sink.get("prompt_tokens", 0) + 100
            sink["completion_tokens"] = sink.get("completion_tokens", 0) + 10
        return {"content": "synthesis"}


async def test_the_routing_call_is_billed_to_the_caller() -> None:
    # The routing call is a real cost of the routed arms. Spending it off-book made those arms
    # look cheaper than they are, in the very comparison against the generalist they exist to
    # inform — so classify_question must report its usage, not swallow it.
    client = _BillingChatClient()
    usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}

    routed = await classify_question(client, "who is this?", usage_sink=usage)

    assert routed == "synthesis"
    assert client.calls == 1
    assert usage == {"prompt_tokens": 100, "completion_tokens": 10}


# — N12: kits and the live registry must be one world —


def test_every_kit_resolves_fully_against_live_registry() -> None:
    full = build_shared_core_registry()

    for class_name, spec in INTENT_CLASSES.items():
        scoped = registry_for_class(full, class_name)

        assert sorted(scoped.names()) == sorted(spec["tools"]), class_name


def test_kit_naming_unregistered_tool_raises() -> None:
    async def _noop(session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        return {}

    only_search = ToolRegistry(
        [ToolSpec(name="search_emails", description="d", parameters={}, executor=_noop)]
    )

    with pytest.raises(UnknownToolError):
        registry_for_class(only_search, "synthesis")  # kit names tools this registry lacks


def test_generalist_and_unknown_class_return_full_registry() -> None:
    full = build_shared_core_registry()

    assert registry_for_class(full, None) is full
    assert registry_for_class(full, "not-a-class") is full


# — N13: classification takes the CONCLUSION, never the first dict-order mention —


async def test_bare_content_class_taken_verbatim() -> None:
    routed = await classify_question(
        _FakeChatClient({"content": "synthesis"}),  # type: ignore[arg-type]
        "q",
    )

    assert routed == "synthesis"


@pytest.mark.parametrize(
    ("prose", "expected"),
    [
        # A class name INSIDE a longer token is not a choice. Without word boundaries the
        # scan matched these and routed on a substring; no fixture contained one, so the
        # boundary rule could be deleted with every router test still green.
        ("This is a synthesis question about aggregations.", "synthesis"),
        ("not aggregation_style; entity_lookups here", None),
    ],
)
async def test_a_class_name_inside_a_longer_token_is_not_a_classification(
    prose: str, expected: str | None
) -> None:
    routed = await classify_question(_FakeChatClient({"content": prose}), "q")  # type: ignore[arg-type]

    assert routed == expected


async def test_prose_content_takes_last_mention_not_dict_order() -> None:
    message = {
        "content": (
            "This is not an entity_lookup question about contact details; "
            "it asks to assemble a full picture, so synthesis."
        )
    }

    routed = await classify_question(_FakeChatClient(message), "q")  # type: ignore[arg-type]

    assert routed == "synthesis"


async def test_rejection_prose_returns_the_conclusion() -> None:
    message = {"content": "Not aggregation, not content_search -> existence_check"}

    routed = await classify_question(_FakeChatClient(message), "q")  # type: ignore[arg-type]

    assert routed == "existence_check"


async def test_reasoning_last_mention_used_when_content_empty() -> None:
    message = {
        "content": "",
        "reasoning": "could be entity_lookup or aggregation, but really synthesis",
    }

    routed = await classify_question(_FakeChatClient(message), "q")  # type: ignore[arg-type]

    assert routed == "synthesis"


async def test_no_class_mentioned_returns_none() -> None:
    routed = await classify_question(
        _FakeChatClient({"content": "hello there"}),  # type: ignore[arg-type]
        "q",
    )

    assert routed is None


async def test_router_model_failure_returns_none() -> None:
    routed = await classify_question(_ExplodingChatClient(), "q")  # type: ignore[arg-type]

    assert routed is None
