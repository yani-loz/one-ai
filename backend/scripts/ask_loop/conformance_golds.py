"""
Role: The gold schemas the grader conformance suite grades against — pure data, one named
      constant per shape (count, no-data, citation-bearing, entity, free text, date).
Used by: scripts.ask_loop.conformance_cases (the assertions), scripts.ask_loop.conformance.
Depends on: nothing — plain dict literals, importable with no DB and no network.
Key invariants:
  - Data only. A gold says what the right answer IS; whether an answer matches is the grader's
    job and asserting that is conformance_cases' job.
  - Each constant is named for the SHAPE it pins, not for the case that first needed it, so a
    new case can reuse one instead of pasting a fourteenth near-identical dict inline.
  - `gold_status: authored` on every graded gold — the grader ignores anything else, which is
    what GOLD_DRAFT_UNGRADED exists to prove.
"""

from __future__ import annotations

from typing import Any


def _gold(intent_class: str, gold: dict[str, Any]) -> dict[str, Any]:
    """Wrap a gold body in the authored-question envelope the grader expects."""
    return {
        "qid": "T",
        "gold_status": "authored",
        "intent_class": intent_class,
        "split": "dev",
        "gold": gold,
        "question": "?",
    }


GOLD_COUNT_42 = _gold(
    "aggregation",
    {
        "state": "answerable",
        "answer_type": "count",
        "expected": {"value": 42},
        "evidence": {"min_citations": 0},
    },
)

GOLD_NO_DATA = _gold("existence_check", {"state": "no_data"})

# min_citations 1: this is the shape where an invented [id: <uuid>] must fail.
GOLD_CITED_ENTITY = _gold(
    "content_search",
    {
        "state": "answerable",
        "answer_type": "entity",
        "expected": {"canonical": "order.pdf"},
        "evidence": {"min_citations": 1},
    },
)

GOLD_COUNT_9_TOLERANT = _gold(
    "aggregation",
    {
        "state": "answerable",
        "answer_type": "count",
        "expected": {"value": 9, "tolerance": 4},
        "evidence": {"min_citations": 0},
    },
)

# The corpus is bilingual: the canonical name is Cyrillic and the answer may transliterate.
GOLD_ENTITY_TRANSLITERATED = _gold(
    "entity_lookup",
    {
        "state": "answerable",
        "answer_type": "entity",
        "expected": {"canonical": "Мария Петрова", "alternatives": ["Maria Petrova"]},
        "evidence": {"min_citations": 0},
    },
)

GOLD_COUNT_ZERO = _gold(
    "aggregation",
    {"state": "answerable", "answer_type": "count", "expected": {"value": 0, "tolerance": 0}},
)

GOLD_COUNT_9 = _gold(
    "aggregation",
    {"state": "answerable", "answer_type": "count", "expected": {"value": 9, "tolerance": 4}},
)

# 'Beyond' as an alternative is the point: it is a real English word, so a substring match
# would find it inside "anything beyond that".
GOLD_ENTITY_POLARITY = _gold(
    "entity_lookup",
    {
        "state": "answerable",
        "answer_type": "entity",
        "expected": {"canonical": "Boyan Bonev", "alternatives": ["Beyond"]},
    },
)

# Short acronyms are how this corpus's counterparties are actually written.
GOLD_ENTITY_SHORT_ACRONYM = _gold(
    "entity_lookup",
    {
        "state": "answerable",
        "answer_type": "entity",
        "expected": {"canonical": "GBS", "alternatives": ["Glavbulgarstroy"]},
    },
)

GOLD_FREE_TEXT = _gold(
    "synthesis",
    {"state": "answerable", "answer_type": "text", "evidence": {"min_citations": 1}},
)

GOLD_COUNT_2962 = _gold(
    "aggregation",
    {"state": "answerable", "answer_type": "count", "expected": {"value": 2962, "tolerance": 150}},
)

GOLD_DATE = _gold(
    "temporal_activity",
    {
        "state": "answerable",
        "answer_type": "date",
        "expected": {"value": "2024-09-10", "tolerance_days": 2},
    },
)

# Not authored: the grader must return a uniform row for it rather than skipping it, or the
# summary tally KeyErrors on intent_class.
GOLD_DRAFT_UNGRADED: dict[str, Any] = {
    "qid": "U",
    "gold_status": "draft",
    "intent_class": "aggregation",
    "split": "dev",
    "question": "?",
}
