"""
Role: The grader conformance CASES — every historical grading bug, expressed as an assertion
      that fails if the fix is reverted. Grouped by the rule each family pins.
Used by: scripts.ask_loop.conformance (the runner, which supplies `check` and the tally).
Depends on: scripts.ask_loop.grade + scripts.ask_loop.answer_extraction (pure functions),
      scripts.ask_loop.conformance_golds (the gold schemas). No DB, no network.
Key invariants:
  - EVERY case cites the measurement that produced it. These are not hypotheticals: each one is
    a real stored run that passed or failed for the wrong reason. A case without a recorded
    cause is a case nobody can safely delete.
  - The suite pins BOTH directions. A grader that flatters (passes a wrong answer) and a grader
    that slanders (fails a right one) are equally broken, and the round-3/4 families exist
    because the second kind sent the loop chasing phantom regressions.
  - Cases assert VERDICTS, never internal grader state, so the rules can be reimplemented.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from scripts.ask_loop.answer_extraction import _extract_dates, _extract_numbers
from scripts.ask_loop.conformance_golds import (
    GOLD_CITED_ENTITY,
    GOLD_COUNT_9,
    GOLD_COUNT_9_TOLERANT,
    GOLD_COUNT_42,
    GOLD_COUNT_2962,
    GOLD_COUNT_ZERO,
    GOLD_DATE,
    GOLD_DRAFT_UNGRADED,
    GOLD_ENTITY_POLARITY,
    GOLD_ENTITY_SHORT_ACRONYM,
    GOLD_ENTITY_TRANSLITERATED,
    GOLD_FREE_TEXT,
    GOLD_NO_DATA,
)
from scripts.ask_loop.grade import grade_one

CheckFn = Callable[[str, bool], None]

_REAL_ID = "22222222-2222-2222-2222-222222222222"
_REAL_PAYLOAD = f'[{{"id": "{_REAL_ID}"}}]'


def _verdict(
    answer: str, gold: dict[str, Any], tool_calls: list[dict[str, Any]] | None = None
) -> str:
    """The grader's verdict for one answer against one gold."""
    record = {"qid": gold["qid"], "answer": answer, "tool_calls": tool_calls or []}
    return str(grade_one(record, gold)["verdict"])


def check_number_extraction(check: CheckFn) -> None:
    """Real quantities survive extraction; every digit-leaking context is stripped.

    Each strip is probed on its own. The uuid strip had no probe for a while: headline binding
    happened to make its case fail with the right verdict through the wrong mechanism, so
    deleting the strip changed nothing the suite could see.
    """
    check("count basic", _extract_numbers("We have 42 threads and 7 vendors.") == [42, 7])
    check("count thousands", 5893 in _extract_numbers("about 5,893 emails total"))
    check("timestamp digits", 55 not in _extract_numbers("arrived at 16:31:55 that day"))
    check("iso date digits", 2026 not in _extract_numbers("sent on 2026-07-04 to them"))
    check("list indices", 5 not in _extract_numbers("\n1. a\n5. b\n7. c\n"))
    check(
        "uuid digits never become numbers",
        446655440000
        not in _extract_numbers("I found 7 PDFs [id: 550e8400-0000-41d4-a716-446655440000]."),
    )
    check(
        "a prose date never becomes a number",
        2026 not in _extract_numbers("From June 2026 we counted 9 threads."),
    )
    check(
        "a bare year in date position never becomes a number",
        2026 not in _extract_numbers("In 2026 the archive holds 2962 threads."),
    )


def check_count_rules(check: CheckFn) -> None:
    """A count gold binds to the FIRST number of the headline claim, and nothing else."""
    check("count pass", _verdict("There are 42 threads.", GOLD_COUNT_42) == "pass")
    check("count fail", _verdict("There are 41 threads.", GOLD_COUNT_42) == "fail")
    check(
        "count refuse+list fail",
        _verdict("Cannot count. Items:\n41. a\n42. b", GOLD_COUNT_42) == "fail",
    )
    # 4 is the headline; 12/11/9 are incidental body numbers (verifier MUT11b).
    check(
        "count headline binding",
        _verdict(
            "We have at least 4 active clients. Details: Alpha sent 12 inbound messages "
            "and Beta sent 11 messages over 9 threads.",
            GOLD_COUNT_9_TOLERANT,
        )
        == "fail",
    )
    # A mandated [id: <uuid>] citation carries all-digit groups; one was read as the count, so
    # an answer of 7 satisfied a gold of 0 (red-team round 2).
    check(
        "citation uuid digits are not the count",
        _verdict(
            "I found 7 PDF attachments this month [id: 550e8400-0000-41d4-a716-446655440000].",
            GOLD_COUNT_ZERO,
            [{"result_payload": "550e8400-0000-41d4-a716-446655440000"}],
        )
        == "fail",
    )
    # The model answered 25; a trailing qualifier happened to contain 9.
    check(
        "a later number cannot rescue the headline",
        _verdict("We have 25 active clients; the top 9 are listed below.", GOLD_COUNT_9) == "fail",
    )
    check(
        "an explicit inability is not a count",
        _verdict("I could not compute a total, but the search returned 9 matches.", GOLD_COUNT_9)
        == "pending_critic",
    )
    check(
        "a correct count still passes",
        _verdict("There are 10 active clients.", GOLD_COUNT_9) == "pass",
    )
    check(
        "a list index is not the count",
        _verdict("Top senders:\n1. Acme sent 9 emails\n2. Other sent 2 emails", GOLD_COUNT_9)
        == "pass",
    )


def check_count_false_failures(check: CheckFn) -> None:
    """Round 3/4: the grader must not FAIL what is right, either.

    A prose date before the count bound the claim to the YEAR, so the same answer passed or
    failed by date FORMAT alone. Both shapes here are taken from stored runs.
    """
    check(
        "a prose date does not become the count",
        _verdict(
            "Based on the email archive from June 2026, I can identify 9 unique external "
            "email addresses with ongoing activity.",
            GOLD_COUNT_9,
        )
        == "pass",
    )
    check(
        "a messy prose date does not become the count",
        _verdict(
            "Based on a search from June 4-4, 2026, I can identify 9 unique addresses.",
            GOLD_COUNT_9,
        )
        == "pass",
    )
    # A trailing honesty note is the candour the prompt asks for, not a refusal of the claim.
    check(
        "a trailing scoping note does not void the count",
        _verdict(
            "There are 10 active clients. Note: the remaining addresses could not be matched "
            "to names or roles in the available data.",
            GOLD_COUNT_9,
        )
        == "pass",
    )
    # A bare year in date position was read as the count, in BOTH directions.
    check(
        "a bare year is not the count",
        _verdict("In 2026 the archive holds 2,962 email threads.", GOLD_COUNT_2962) == "pass",
    )
    check(
        "a bare year cannot rescue a wrong count",
        _verdict("In 2026 the archive holds 118 email threads.", GOLD_COUNT_2962) == "fail",
    )
    # 24 of 24 stored rows on one dev gold answered a zero-count question correctly IN WORDS
    # and were scored wrong, because the branch demanded a literal digit.
    check(
        "a zero count answered in words passes",
        _verdict("No PDF attachments were received this month.", GOLD_COUNT_ZERO) == "pass",
    )
    check(
        "a zero count answered with a digit still passes",
        _verdict("There are 0 PDF attachments this month.", GOLD_COUNT_ZERO) == "pass",
    )
    check(
        "asserting data does not satisfy a zero gold",
        _verdict("I found 7 PDF attachments this month.", GOLD_COUNT_ZERO) == "fail",
    )


def check_no_data_rules(check: CheckFn) -> None:
    """Honesty on a no_data gold is the ABSENCE of a data claim, not a negative phrase."""
    check(
        "no-data honest pass",
        _verdict("No emails were found on that.", GOLD_NO_DATA) == "pass",
    )
    check(
        "no-data fabricated citation fail",
        _verdict(
            "Yes [id: 11111111-1111-1111-1111-111111111111].",
            GOLD_NO_DATA,
            [{"result_payload": "{}"}],
        )
        == "fail",
    )
    # Cross-vendor N6: a data-ASSERTING count must never match the no-data regex.
    check(
        "'10 emails' is not a refusal",
        _verdict("Yes — I found 10 emails from the client about the tender.", GOLD_NO_DATA)
        == "pending_critic",
    )
    check(
        "'0 emails' still honest",
        _verdict("The search returned 0 emails on that topic.", GOLD_NO_DATA) == "pass",
    )
    check(
        "'zero results' still honest",
        _verdict("There were zero results for every variant tried.", GOLD_NO_DATA) == "pass",
    )
    # An INCIDENTAL negative clause must not launder a data-asserting answer into 'honest'.
    # Three real shapes the bare-regex honesty gate auto-passed (fix-wave review, N6 follow-up).
    for label, answer in (
        (
            "trailing 'no attachments'",
            "Yes — I found 10 emails from the client, but no attachments were included.",
        ),
        (
            "mid-answer 'contains no data'",
            "Yes, there are 12 threads. The archive contains no data on pricing though.",
        ),
        (
            "'not found' after a count",
            "I retrieved 5 messages; the invoice was not found among them.",
        ),
    ):
        check(f"data assertion beats {label}", _verdict(answer, GOLD_NO_DATA) == "pending_critic")


def check_citation_fidelity(check: CheckFn) -> None:
    """A cited id must have appeared in that run's own tool RESULTS — never its arguments."""
    check(
        "citation real pass",
        _verdict(
            f"Found order.pdf [id: {_REAL_ID}].",
            GOLD_CITED_ENTITY,
            [{"result_payload": _REAL_PAYLOAD}],
        )
        == "pass",
    )
    check(
        "citation invented fail",
        _verdict(
            "Found order.pdf [id: 33333333-3333-3333-3333-333333333333].",
            GOLD_CITED_ENTITY,
            [{"result_payload": _REAL_PAYLOAD}],
        )
        == "fail",
    )
    # Cross-vendor N5: a model-INVENTED id passed into a tool's ARGUMENTS is not evidence.
    check(
        "argument uuid is not evidence",
        _verdict(
            "Found order.pdf [id: 44444444-4444-4444-4444-444444444444].",
            GOLD_CITED_ENTITY,
            [
                {
                    "name": "get_email",
                    "arguments": {"email_id": "44444444-4444-4444-4444-444444444444"},
                    "result_payload": '{"found": false, "note": "no email with the requested id"}',
                }
            ],
        )
        == "fail",
    )
    # Free text is the class where provenance matters most, and was the only class with no pin.
    check(
        "an invented citation fails even on a pending_critic row",
        _verdict(
            "The relationship began in spring [id: 99999999-9999-9999-9999-999999999999].",
            GOLD_FREE_TEXT,
            [{"result_payload": "{}"}],
        )
        == "fail",
    )
    check(
        "a genuine citation still reaches the critic",
        _verdict(
            f"The relationship began in spring [id: {_REAL_ID}].",
            GOLD_FREE_TEXT,
            [{"result_payload": _REAL_PAYLOAD}],
        )
        == "pending_critic",
    )


def check_entity_rules(check: CheckFn) -> None:
    """An entity is matched as a WORD; polarity is the critic's call, not a substring's."""
    check(
        "entity transliteration pass",
        _verdict("The contact is Maria Petrova.", GOLD_ENTITY_TRANSLITERATED) == "pass",
    )
    check(
        "naming the entity while refusing is not an answer",
        _verdict("There is no information about Boyan Bonev in the archive.", GOLD_ENTITY_POLARITY)
        == "pending_critic",
    )
    check(
        "a substring inside another word is not the entity",
        _verdict("Anything beyondthat is unavailable in this dataset.", GOLD_ENTITY_POLARITY)
        == "fail",
    )
    check(
        "a real identification still passes",
        _verdict("Boyan Bonev is the head of procurement.", GOLD_ENTITY_POLARITY) == "pass",
    )
    check(
        "a short acronym is recalled",
        _verdict("The main counterparty is GBS with 5 people.", GOLD_ENTITY_SHORT_ACRONYM)
        == "pass",
    )
    check(
        "a lowercase fragment is not the acronym",
        _verdict(
            "The gbs of that measure is unrelated to any counterparty.", GOLD_ENTITY_SHORT_ACRONYM
        )
        == "fail",
    )


def check_date_rules(check: CheckFn) -> None:
    """Both written orders parse, and dates get the same headline discipline as counts."""
    check(
        "month-first english date",
        date(2026, 5, 26)
        in _extract_dates("The last contact was on May 26, 2026 from the client."),
    )
    check(
        "day-first date still parses",
        date(2026, 3, 20) in _extract_dates("It was accepted on 20 March 2026."),
    )
    check(
        "the headline date decides",
        _verdict("The last message was on 2024-09-10.", GOLD_DATE) == "pass",
    )
    check(
        "a later date cannot rescue a wrong headline",
        _verdict(
            "The last message was on 2025-02-10. An unrelated thread mentions 2024-09-10, "
            "which is not the answer.",
            GOLD_DATE,
        )
        == "fail",
    )


def check_row_schema(check: CheckFn) -> None:
    """Every row carries the uniform schema — the summary tally must never KeyError (N9)."""
    # An infrastructure failure is not a model answer (N10 follow-up).
    errored = grade_one(
        {"qid": "T", "answer": "", "error": "ReaderModelError: HTTP 503", "tool_calls": []},
        GOLD_NO_DATA,
    )
    check("errored record is not graded", errored["verdict"] == "error")
    check("errored row keeps intent_class", "intent_class" in errored)

    ungraded = grade_one({"qid": "U", "answer": "x", "tool_calls": []}, GOLD_DRAFT_UNGRADED)
    check("ungraded row has intent_class", "intent_class" in ungraded)
    check("ungraded row has split", "split" in ungraded)
    check("ungraded verdict", ungraded["verdict"] == "ungraded")


ALL_CASE_GROUPS: tuple[Callable[[CheckFn], None], ...] = (
    check_number_extraction,
    check_count_rules,
    check_count_false_failures,
    check_no_data_rules,
    check_citation_fidelity,
    check_entity_rules,
    check_date_rules,
    check_row_schema,
)
