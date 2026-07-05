"""
Role: Runnable grader conformance suite for the ask-tools loop (verifier CKPT1 finding 3) —
      pins every known grading rule and false-positive regression WITHOUT pytest (pytest
      truncates the dev corpus). Run: `uv run python -m scripts.ask_loop.conformance`.
Used by: the loop operator before/after any grader change; the verifier re-runs it.
Depends on: scripts.ask_loop.grade (pure functions — no DB, no network).
Key invariants: exits non-zero on ANY failed case; every historical grader bug gets a case here.
"""

from __future__ import annotations

from scripts.ask_loop.grade import _extract_numbers, grade_one

CASES_PASSED = 0


def check(name: str, condition: bool) -> None:
    """Assert one conformance case; raise on failure."""
    global CASES_PASSED
    if not condition:
        raise SystemExit(f"CONFORMANCE FAIL: {name}")
    CASES_PASSED += 1


def main() -> int:
    """Run all conformance cases; print the tally."""
    # — number extraction: real values survive —
    check("count basic", _extract_numbers("We have 42 threads and 7 vendors.") == [42, 7])
    check("count thousands", 5893 in _extract_numbers("about 5,893 emails total"))
    # — verifier-confirmed false-positive sources are stripped —
    check("timestamp digits", 55 not in _extract_numbers("arrived at 16:31:55 that day"))
    check("iso date digits", 2026 not in _extract_numbers("sent on 2026-07-04 to them"))
    check("list indices", 5 not in _extract_numbers("\n1. a\n5. b\n7. c\n"))

    gold_count = {
        "qid": "T", "gold_status": "authored", "intent_class": "aggregation", "split": "dev",
        "gold": {"state": "answerable", "answer_type": "count",
                 "expected": {"value": 42}, "evidence": {"min_citations": 0}}, "question": "?",
    }
    check("count pass", grade_one(
        {"qid": "T", "answer": "There are 42 threads.", "tool_calls": []}, gold_count
    )["verdict"] == "pass")
    check("count fail", grade_one(
        {"qid": "T", "answer": "There are 41 threads.", "tool_calls": []}, gold_count
    )["verdict"] == "fail")
    check("count refuse+list fail", grade_one(
        {"qid": "T", "answer": "Cannot count. Items:\n41. a\n42. b", "tool_calls": []},
        gold_count,
    )["verdict"] == "fail")

    gold_nd = {"qid": "T", "gold_status": "authored", "intent_class": "existence_check",
               "split": "dev", "gold": {"state": "no_data"}, "question": "?"}
    check("no-data honest pass", grade_one(
        {"qid": "T", "answer": "No emails were found on that.", "tool_calls": []}, gold_nd
    )["verdict"] == "pass")
    check("no-data fabricated citation fail", grade_one(
        {"qid": "T", "answer": "Yes [id: 11111111-1111-1111-1111-111111111111].",
         "tool_calls": [{"result_payload": "{}"}]}, gold_nd,
    )["verdict"] == "fail")

    gold_cit = {
        "qid": "T", "gold_status": "authored", "intent_class": "content_search", "split": "dev",
        "gold": {"state": "answerable", "answer_type": "entity",
                 "expected": {"canonical": "order.pdf"}, "evidence": {"min_citations": 1}},
        "question": "?",
    }
    check("citation real pass", grade_one(
        {"qid": "T", "answer": "Found order.pdf [id: 22222222-2222-2222-2222-222222222222].",
         "tool_calls": [{"result_payload": '[{"id": "22222222-2222-2222-2222-222222222222"}]'}]},
        gold_cit,
    )["verdict"] == "pass")
    check("citation invented fail", grade_one(
        {"qid": "T", "answer": "Found order.pdf [id: 33333333-3333-3333-3333-333333333333].",
         "tool_calls": [{"result_payload": '[{"id": "22222222-2222-2222-2222-222222222222"}]'}]},
        gold_cit,
    )["verdict"] == "fail")

    gold_ent = {
        "qid": "T", "gold_status": "authored", "intent_class": "entity_lookup", "split": "dev",
        "gold": {"state": "answerable", "answer_type": "entity",
                 "expected": {"canonical": "Мария Петрова", "alternatives": ["Maria Petrova"]},
                 "evidence": {"min_citations": 0}}, "question": "?",
    }
    check("entity transliteration pass", grade_one(
        {"qid": "T", "answer": "The contact is Maria Petrova.", "tool_calls": []}, gold_ent
    )["verdict"] == "pass")

    print(f"grader conformance: {CASES_PASSED}/{CASES_PASSED} PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
