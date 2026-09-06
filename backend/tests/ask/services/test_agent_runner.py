"""
Role: Unit tests for the runner's budget-aware observation serializer (_fit_payload, R2/N4) —
      a truncated tool result must stay VALID JSON with every control/anchor field intact;
      only bulk rows and long strings may be sacrificed, always with an explicit marker.
      Pure string/dict transforms — no DB, no network, no model.
Used by: pytest (tests/ask/services).
Depends on: app.ask.services.agent_runner (_fit_payload, _TOOL_RESULT_CHAR_CAP).
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

from app.ask.exceptions import ToolExecutionError
from app.ask.services.agent_runner import (
    _TOOL_RESULT_CHAR_CAP,
    AskAgentRunner,
    ToolCallRecord,
    _fit_payload,
)
from app.ask.tools.registry import ToolRegistry
from app.ask.tools.tool_helpers import _parse_iso_date, parse_id_arg


def _search_envelope(
    *, n_results: int, n_listing: int, listing_row_chars: int = 90
) -> dict[str, Any]:
    """A realistic search_emails envelope: control fields first, bulky results last."""
    return {
        "total_matches": n_listing,
        "date_span": {
            "earliest": {"which": "earliest", "id": "e" * 36, "sent_at": "2026-01-01"},
            "latest": {"which": "latest", "id": "l" * 36, "sent_at": "2026-06-30"},
        },
        "sent_split": {"inbound": 20, "outbound": 14, "unknown": 0},
        "listing_complete": True,
        "listing": [
            f"2026-01-{(i % 28) + 1:02d} · sender{i}@counterparty.test · "
            + "s" * max(0, listing_row_chars - 50)
            + f" · [id: {i:036d}]"
            for i in range(n_listing)
        ],
        "results": [
            {
                "id": f"{i:036d}",
                "sent_at": "2026-01-15T12:00:00+00:00",
                "from_address": f"sender{i}@counterparty.test",
                "subject": f"Quarterly settlement discussion round {i}",
                "snippet": "x" * 240,
                "subject_hit": False,
            }
            for i in range(n_results)
        ],
    }


def test_fit_payload_small_result_is_untouched() -> None:
    result = {"total_matches": 2, "results": [{"id": "a"}, {"id": "b"}]}

    payload = _fit_payload(result)

    assert json.loads(payload) == result


def test_fit_payload_oversized_envelope_stays_valid_json_with_control_fields() -> None:
    payload = _fit_payload(_search_envelope(n_results=10, n_listing=34))

    assert len(payload) <= _TOOL_RESULT_CHAR_CAP
    parsed = json.loads(payload)  # the old blind slice produced unparseable JSON
    assert parsed["total_matches"] == 34
    assert "date_span" in parsed
    assert "sent_split" in parsed
    assert "listing_complete" in parsed
    assert "truncated" in parsed  # the cut is explicit, never silent


def test_fit_payload_drops_result_rows_before_touching_the_listing() -> None:
    # Overshoot small enough that dropping snippet rows alone fits the budget.
    envelope = _search_envelope(n_results=10, n_listing=20)

    parsed = json.loads(_fit_payload(envelope))

    assert len(parsed["listing"]) == 20  # the completeness contract survives intact
    assert parsed["listing_complete"] is True
    assert len(parsed["results"]) < 10
    assert parsed["truncated"]["dropped_rows"].keys() == {"results"}


@pytest.mark.parametrize("total", [8, 12, 20, 24, 30])
def test_listing_enumeration_survives_truncation_up_to_the_budget(total: int) -> None:
    # The completeness contract is the point of `listing`; `results` is a ranked PAGE of the
    # same set and therefore the redundant one. Size-ordering alone chose the listing (it is
    # the larger field) and one round charged it the whole overshoot — so 40- and 50-match
    # searches came back with the enumeration entirely gone while the page sat untouched.
    envelope = _search_envelope(n_results=min(10, total), n_listing=total)

    parsed = json.loads(_fit_payload(envelope))

    assert len(parsed["listing"]) == total
    assert parsed["listing_complete"] is True
    assert len(parsed.get("results", [])) < min(10, total) or total <= 8


@pytest.mark.parametrize("total", [40, 50])
@pytest.mark.parametrize("row_chars", [90, 140, 200])
def test_oversized_listing_degrades_gracefully_and_stays_honest(
    total: int, row_chars: int
) -> None:
    # Above the budget the enumeration genuinely cannot always fit. Whatever happens, two
    # things must hold: most rows survive, and the completeness flag matches reality — the
    # flag is what tells the model whether it may answer "all" from this payload.
    envelope = _search_envelope(n_results=10, n_listing=total, listing_row_chars=row_chars)

    payload = _fit_payload(envelope)
    parsed = json.loads(payload)

    kept = len(parsed["listing"])
    assert kept > 0, "the enumeration was emptied instead of trimmed"
    assert parsed["listing_complete"] is (kept == total)
    # Rows may only be dropped to BUY space. If the payload comes back well under budget,
    # rows were sacrificed for nothing — which is exactly what over-charging one field did.
    assert len(payload) >= _TOOL_RESULT_CHAR_CAP * 0.85


@pytest.mark.parametrize("row_chars", [90, 140, 200])
@pytest.mark.parametrize("total", [30, 40, 50])
def test_the_sample_page_is_always_spent_before_the_enumeration(
    total: int, row_chars: int
) -> None:
    # THE invariant, stated directly instead of inferred from row counts: `results` is a
    # ranked PAGE of the set `listing` enumerates, so its rows are the redundant ones. If a
    # single listing row was dropped while any results row survived, the budget was spent in
    # the wrong order. Asserting only "listing is non-empty" let a revert gut the enumeration
    # to 4 of 50 rows while the page kept 9 of 10 — with every test still green.
    envelope = _search_envelope(n_results=10, n_listing=total, listing_row_chars=row_chars)

    parsed = json.loads(_fit_payload(envelope))

    if len(parsed["listing"]) < total:
        assert not parsed.get("results"), (
            f"{total - len(parsed['listing'])} enumeration rows were dropped while "
            f"{len(parsed.get('results', []))} redundant page rows survived"
        )


def test_control_scalars_survive_an_untrimmable_result() -> None:
    # The "control fields always survive" promise, on the shape that reaches the last-resort
    # branch. The earlier fixture had no scalars at all, so emptying that branch entirely
    # changed nothing and the promise was untested.
    result: dict[str, Any] = {"total_matches": 4321, "listing_complete": False}
    result.update({f"k{i}": {"a": "v" * 60, "b": i} for i in range(120)})

    parsed = json.loads(_fit_payload(result))

    assert parsed["total_matches"] == 4321
    assert parsed["listing_complete"] is False
    assert "truncated" in parsed


def test_no_single_round_may_empty_a_field() -> None:
    # A field that still has rows to give must never be wiped while another candidate is
    # untouched: that is how one round destroyed a 50-row enumeration.
    envelope = _search_envelope(n_results=10, n_listing=50, listing_row_chars=200)

    parsed = json.loads(_fit_payload(envelope))

    assert parsed["listing"], "the enumeration was emptied outright"


def test_fit_payload_flips_listing_complete_when_listing_rows_are_cut() -> None:
    # Listing alone exceeds the budget: rows must go AND the completeness flag must stop
    # claiming an exhaustive enumeration the model can no longer see.
    envelope = _search_envelope(n_results=10, n_listing=50, listing_row_chars=200)

    parsed = json.loads(_fit_payload(envelope))

    assert len(parsed["listing"]) < 50
    assert parsed["listing_complete"] is False


def test_fit_payload_shortens_long_strings_for_full_body_results() -> None:
    # get_email shape: one giant body string, no top-level lists to drop.
    result = {"found": True, "subject": "contract", "body_text": "b" * 20_000}

    payload = _fit_payload(result)

    assert len(payload) <= _TOOL_RESULT_CHAR_CAP
    parsed = json.loads(payload)
    assert parsed["found"] is True
    assert parsed["body_text"].endswith("... [truncated]")


def test_fit_payload_keeps_small_complete_lists_while_shortening_the_giant_string() -> None:
    # Trimming by key NAME emptied get_email's recipients/attachments — both promised
    # complete by the tool contract — to protect a body it then had to shorten anyway.
    # Trimming by SIZE takes the body first and leaves the small lists whole.
    result = {
        "found": True,
        "subject": "contract",
        "body_text": "b" * 20_000,
        "recipients": [{"kind": "to", "address": f"p{i}@x.test"} for i in range(6)],
        "attachments": [{"id": f"a{i}", "filename": f"f{i}.pdf"} for i in range(3)],
    }

    parsed = json.loads(_fit_payload(result))

    assert len(parsed["recipients"]) == 6
    assert len(parsed["attachments"]) == 3
    assert parsed["body_text"].endswith("... [truncated]")


def test_fit_payload_moves_next_offset_back_when_document_text_is_shortened() -> None:
    # get_attachment pages on next_offset; a shortened text with an unchanged cursor makes
    # the next call start past characters the model never saw.
    # A real get_attachment page: 5000 extracted chars from offset 5000, so next_offset is
    # 10000. Line breaks double under JSON escaping, which is how a within-cap page still
    # overflows the observation budget.
    page = ("z" * 4 + "\n") * 1000
    result = {
        "found": True,
        "filename": "contract.pdf",
        "offset": 5000,
        "total_chars": 40_000,
        "next_offset": 10_000,
        "text": page,
    }

    parsed = json.loads(_fit_payload(result))

    kept = parsed["text"].removesuffix("... [truncated]")
    assert len(kept) < len(page)  # the page really was shortened
    assert parsed["next_offset"] == parsed["offset"] + len(kept)
    assert parsed["next_offset"] < 10_000  # the cursor never points past what was shown


def test_untrimmable_shape_still_yields_valid_json() -> None:
    # A result made only of small scalar/dict fields has nothing this function knows how to
    # shrink. The old fallback sliced the JSON string — handing the model an observation it
    # could not parse, which is the exact failure the structural rewrite exists to prevent.
    result = {f"k{i}": {"a": "v" * 60, "b": i} for i in range(120)}

    payload = _fit_payload(result)

    parsed = json.loads(payload)  # must parse, whatever else it says
    assert "truncated" in parsed
    assert len(payload) <= _TOOL_RESULT_CHAR_CAP


@pytest.mark.parametrize(
    "bad_id",
    ["not-a-uuid", "1234", "", "   ", "44444444-4444-4444-4444-44444444444", "'; DROP--"],
)
def test_a_malformed_id_is_rejected_before_it_reaches_the_database(bad_id: str) -> None:
    # A bad uuid sent to Postgres raises a driver error that ABORTS the transaction, so one
    # hallucinated id from the model killed every remaining tool call of the question.
    with pytest.raises(ToolExecutionError):
        parse_id_arg({"email_id": bad_id}, "email_id")


def test_a_rejected_id_is_never_echoed_back() -> None:
    # The error message lands in the observation, and a caller-supplied uuid must not re-enter
    # the evidence base through it.
    invented = "44444444-4444-4444-4444-444444444444x"

    with pytest.raises(ToolExecutionError) as raised:
        parse_id_arg({"email_id": invented}, "email_id")

    assert "44444444" not in str(raised.value)


def test_a_well_formed_id_passes_through_normalised() -> None:
    canonical = "44444444-4444-4444-4444-444444444444"

    assert parse_id_arg({"email_id": canonical.upper()}, "email_id") == canonical


async def _execute(call: dict[str, Any]) -> tuple[dict[str, Any], list[ToolCallRecord]]:
    """Drive one tool call through the real _execute_call with an empty registry."""
    runner = AskAgentRunner(client=None, registry=ToolRegistry([]), today=date(2026, 1, 1))
    records: list[ToolCallRecord] = []
    observation = await runner._execute_call(None, call, turn=1, records=records)  # noqa: SLF001
    return observation, records


async def test_already_parsed_tool_arguments_do_not_kill_the_question() -> None:
    # The wire format sends `arguments` as a JSON STRING, but a vendor may hand it over
    # already parsed. Assuming the string made json.loads raise TypeError — which was not
    # caught — and one such call ended the whole question. No test reached _execute_call at
    # all, so this failure mode was entirely unpinned.
    call = {"function": {"name": "search_emails", "arguments": {"queries": ["x"]}}}

    observation, records = await _execute(call)

    assert records[0].ok is False  # an unknown tool here, not a crash
    assert "error" in observation["content"]


async def test_an_invented_tool_name_is_not_echoed_back() -> None:
    # The name is fully caller-controlled and the message lands in the observation, so a uuid
    # hidden in it would become citable "tool evidence".
    invented = "read_44444444-4444-4444-4444-444444444444"
    call = {"function": {"name": invented, "arguments": "{}"}}

    observation, _ = await _execute(call)

    assert "44444444" not in observation["content"]


def test_a_malformed_date_argument_is_not_echoed_back() -> None:
    # Same rule for the other caller-supplied argument that reaches an error message.
    with pytest.raises(ToolExecutionError) as raised:
        _parse_iso_date("44444444-4444-4444-4444-444444444444", "date_from")

    assert "44444444" not in str(raised.value)


def test_fit_payload_does_not_mutate_the_caller_result() -> None:
    # The dispatched result object is the tool's, not the runner's — trimming is for the
    # observation string only.
    result = _search_envelope(n_results=10, n_listing=34)

    _fit_payload(result)

    assert len(result["results"]) == 10
    assert len(result["listing"]) == 34
    assert "truncated" not in result
