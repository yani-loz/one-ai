"""
Role: Unit pins for the provenance rules — the test that decides whether a value in a result
      came from the DATABASE or from the caller, and the rule for result column NAMES.
Used by: pytest (tests/ask/tools). Pure functions over plan-expression text: no database.
Depends on: app.ask.tools.sql_provenance.
Key invariants:
  - These are CAUSAL unit pins, and they exist because the mechanism failed SILENTLY once: a
    regex literal was corrupted while the module was being split (a `\\b` became a literal
    backspace), so the call scan matched nothing, every expression was classified as data, and
    the whole anti-fabrication layer was off. Ruff passed. The corpus, which runs through the
    database, would have caught it — but only on a machine with one.
  - Both directions are pinned. A rule that classifies everything as caller-authored redacts
    every legitimate computed column and is just as broken as one that classifies nothing.
"""

from __future__ import annotations

import pytest

from app.ask.tools.sql_provenance import (
    _expression_is_caller_authored,
    _is_plain_column_name,
    redact_computed_values,
)

_AN_ID = "44444444-4444-4444-4444-444444444444"

# Expressions exactly as EXPLAIN (FORMAT JSON, VERBOSE) renders them.
_READS_THE_CORPUS = [
    "count(*)",
    "(count(m.id))",
    "count(DISTINCT m.id)",
    "(lower(subject))",
    "date_trunc('month'::text, sent_at)",
    "m.id",
    "email_message.subject",
    "string_agg(m.subject, ', '::text)",
    "CASE WHEN (direction = 'inbound'::text) THEN 'in'::text ELSE 'out'::text END",
]

_CALLER_AUTHORED = [
    # A fact assembled by a call — no constant rule matches it, and it reads nothing.
    "concat_ws(' '::text, 'Acme', 'owes', '42000', 'EUR')",
    "concat('4444'::text, '4444'::text)",
    # An aggregate over a CONSTANT reads nothing; being an aggregate is not enough.
    "string_agg('Acme owes 42000 EUR'::text, ','::text)",
    "max('Acme owes 42000 EUR'::text)",
    # `count` decorating someone else's expression must not launder it: the entry has to BE a
    # count, not merely mention one.
    "substr(concat_ws(' '::text, 'Acme', 'owes'), 1, (19 + (0 * count(*))))",
    "'Acme signed the renewal'::text",
]


@pytest.mark.parametrize("expression", _READS_THE_CORPUS)
def test_expression_reading_the_corpus_is_not_caller_authored(expression: str) -> None:
    assert _expression_is_caller_authored(expression) is False, (
        f"{expression!r} reads the corpus — classifying it as caller-authored would replace a "
        "legitimate value with a redaction sentinel"
    )


@pytest.mark.parametrize("expression", _CALLER_AUTHORED)
def test_expression_reading_nothing_is_caller_authored(expression: str) -> None:
    assert _expression_is_caller_authored(expression) is True, (
        f"{expression!r} cannot depend on any row, so its value is the caller's own text — "
        "classifying it as data puts a fabricated value in front of the citation grader"
    )


# Every regex in the module, with one string it MUST match and one it must NOT. A corrupted
# literal then fails a named case instead of silently disabling a layer: a neutered pattern
# matches nothing, which leaves most behavioural assertions green (everything looks like data)
# while the guarantee is gone. This is the only check that would have caught the `\b`-became-a-
# backspace incident, and it costs nothing to keep.
_REGEX_PINS = [
    ("_PLAN_CALL", "concat_ws(''::text, '')", "concat_ws ''"),
    ("_COUNT_OUTPUT", "count(*)", "concat_ws(count(1))"),
    ("_SUBPLAN_REFERENCE", "(hashed SubPlan 2)", "m.id"),
    ("_CALLER_LITERAL", "'Acme'", "m.subject"),
    ("_PLAN_REFERENCE", "$0", "($0 + 1)"),
    ("_BARE_COLUMN", "email_message.id", "lower(subject)"),
    ("_PLAIN_COLUMN_NAME", "total_emails", "Acme owes 42000"),
    ("_LONG_DIGIT_RUN", "emails_2024", "top_5"),
    ("PLAN_LITERAL", "'a''b'", "subject"),
    ("_CAST_FRAGMENT", "::text", "subject"),
    ("_ANY_IDENTIFIER", "subject", "42"),
    ("_UUID_CONSTANT", _AN_ID, "not-a-uuid"),
]


@pytest.mark.parametrize(("name", "matches", "does_not_match"), _REGEX_PINS)
def test_every_regex_still_matches_what_it_is_for(
    name: str, matches: str, does_not_match: str
) -> None:
    import app.ask.tools.sql_provenance as provenance

    pattern = getattr(provenance, name)

    assert pattern.search(matches), f"{name} matches nothing — the check it drives is disabled"
    assert not pattern.search(does_not_match), f"{name} matches too much"


def test_the_known_column_inventory_matches_the_view_migration() -> None:
    # `_COUNTERPARTY_SUMMARY_COLUMNS` is hand-maintained and duplicates the projection in the
    # migration, because a VIEW has no ORM model to read. If the view is revised and this list
    # is not, its columns start reading as caller-authored and the view gets REFUSED — an
    # over-rejection that already happened once this round for a different reason. Cheap to
    # check, and it is the same class of drift as the M-Schema card being wrong.
    from pathlib import Path

    from app.ask.tools.sql_provenance import _COUNTERPARTY_SUMMARY_COLUMNS

    migrations = Path(__file__).resolve().parents[3] / "app" / "db" / "migrations" / "versions"
    latest_view = sorted(migrations.glob("*counterparty_summary*.py"))[-1]
    body = latest_view.read_text(encoding="utf-8")

    missing = sorted(name for name in _COUNTERPARTY_SUMMARY_COLUMNS if name not in body)

    assert not missing, (
        f"{latest_view.name} no longer mentions {missing} — the provenance test will read the "
        "view's own columns as caller-authored and refuse every query over it"
    )


def _plan(output: list[str]) -> list[dict[str, object]]:
    """An EXPLAIN (FORMAT JSON) skeleton carrying just the top-level Output list."""
    return [{"Plan": {"Node Type": "Seq Scan", "Output": output}}]


def test_an_id_inside_a_computed_text_value_is_stripped() -> None:
    # The redaction's REMAINING job, now that assembled constants are refused outright before a
    # row is ever fetched: an id that appears inside real email TEXT is not an id the retrieval
    # layer vouched for, and must not be citable just because it reached `rows`.
    rows = [{"excerpt": f"please quote ref {_AN_ID} when replying"}]

    redacted = redact_computed_values(_plan(["substr(body_text, 1, 100)"]), ["excerpt"], rows)

    assert _AN_ID not in str(redacted)


def test_an_id_read_straight_from_a_column_survives() -> None:
    # The other direction, which matters just as much: an id the database returned from its own
    # column IS the evidence the citation grader is supposed to accept. A redaction that eats it
    # makes every real citation unverifiable.
    rows = [{"id": _AN_ID}]

    redacted = redact_computed_values(_plan(["m.id"]), ["id"], rows)

    assert redacted == rows


def test_an_unusable_column_map_redacts_rather_than_guesses() -> None:
    # `ORDER BY` on an unselected column makes the plan-to-column map unusable. With an authored
    # expression present and no way to tell which column it lands in, every computed column is
    # replaced — failing towards redaction, not towards trust.
    plan = _plan(["concat_ws(' '::text, 'Acme', 'owes')", "sent_at"])
    rows = [{"finding": "Acme owes"}]

    redacted = redact_computed_values(plan, ["finding"], rows)

    assert redacted == [{"finding": "[value not from the database]"}]


@pytest.mark.parametrize("name", ["count", "n", "total_emails", "subject", "sent_at", "?column?"])
def test_ordinary_column_names_are_accepted(name: str) -> None:
    assert _is_plain_column_name(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "44444444-4444-4444-4444-444444444444",
        "Acme owes 42000 EUR",
        # Underscores and hyphens carry a sentence just as well as spaces do.
        "Acme_owes_42000_EUR_as_of_2024_03_01",
        "Acme-owes-42000",
        # A long digit run is the part of a fabricated label that asserts something.
        "emails_2024",
    ],
)
def test_caller_authored_column_names_are_refused(name: str) -> None:
    assert _is_plain_column_name(name) is False
