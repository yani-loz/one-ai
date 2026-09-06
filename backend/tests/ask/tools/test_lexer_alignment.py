"""
Role: Deterministic fuzz over the SQL guard's lexer, asserting the one property every other
      check silently depends on — the three views it returns are POSITION-ALIGNED and the same
      length, and every recorded `desc` span addresses real text inside them.
Used by: pytest (tests/ask/tools). Pure text: no database, no network.
Depends on: app.ask.tools.sql_guard (_lex_sql, validate_generated_sql).
Key invariants:
  - This asserts a STRUCTURAL property, not a verdict. Every behavioural test of the guard would
    stay green if the views desynchronised by one character — and the DESC rewrite splices by
    OFFSET into those views, so a one-character drift silently corrupts a statement rather than
    refusing it. That failure has no other detector.
  - The alphabet is the lexer's own hazard list, not random text: quotes, dollar tags, comment
    markers, E-strings, `U&`, and the word-glued forms that defeated earlier versions.
  - Deterministic: a fixed seed, so a failure is reproducible and the suite is repeatable
    (testing.md FIRST). Raising ToolExecutionError is a PASS — fail-closed is the contract for
    anything unterminated.
"""

from __future__ import annotations

import random

import pytest

from app.ask.exceptions import ToolExecutionError
from app.ask.tools.sql_guard import _lex_sql, validate_generated_sql

# The lexer's own hazards. Each token here has broken some version of this lexer or is one
# character away from the shape that did.
_HAZARDS = (
    "'",
    '"',
    "$",
    "$$",
    "$t$",
    "--",
    "/*",
    "*/",
    "\\",
    "e",
    "E",
    "U&",
    ";",
    " ",
    "\n",
    "desc",
    "DESC",
    "limit",
    "''",
    '""',
    "a",
    "1",
    "(",
    ")",
    ",",
    "%",
    "_",
    "select",
)
_FUZZ_CASES = 4000
_MAX_TOKENS = 12


def _fuzzed(seed: int) -> str:
    """One pseudo-random statement built from the hazard alphabet."""
    rng = random.Random(seed)
    return "".join(rng.choice(_HAZARDS) for _ in range(rng.randint(1, _MAX_TOKENS)))


def test_the_three_views_stay_position_aligned_under_fuzz() -> None:
    lexed = 0
    for seed in range(_FUZZ_CASES):
        text = _fuzzed(seed)
        try:
            views = _lex_sql(text)
        except ToolExecutionError:
            continue  # fail-closed on anything unterminated: the contract, not a failure
        lexed += 1

        assert len(views.stripped) == len(views.masked) == len(views.code), (
            f"view desync on {text!r} — the DESC rewrite splices by OFFSET into these views, so "
            "a drift of one character corrupts a statement instead of refusing it"
        )
        for start, end in views.desc_spans:
            assert 0 <= start < end <= len(views.masked), f"span {(start, end)} on {text!r}"
            assert views.masked[start:end].lower() == "desc", (
                f"span {(start, end)} on {text!r} does not address a desc token"
            )

    # If the alphabet ever drifts so that nearly everything is refused, the assertions above stop
    # meaning anything — so assert the fuzz still exercises the accepting path.
    assert lexed > _FUZZ_CASES // 4, f"only {lexed}/{_FUZZ_CASES} inputs lexed; fuzz went blunt"


@pytest.mark.parametrize(
    ("statement", "rewritten"),
    [
        # A DESC inside a literal, an identifier, a quoted name or a comment is NOT a sort key.
        ("SELECT id FROM email_message WHERE subject ILIKE '%desc%' ORDER BY sent_at DESC", 1),
        ("SELECT money$$desc$$ FROM email_message ORDER BY sent_at DESC", 1),
        ('SELECT "desc" FROM email_message ORDER BY sent_at DESC', 1),
        ("SELECT id FROM email_message --desc\nORDER BY sent_at DESC", 1),
        ("SELECT id FROM email_message /*desc*/ORDER BY sent_at DESC", 1),
    ],
)
def test_only_a_real_desc_token_is_rewritten(statement: str, rewritten: int) -> None:
    # M-49: a bare `ORDER BY col DESC` sorts NULLs FIRST, so an un-normalised DESC combined with
    # the appended LIMIT can serve a NULL-valued row as the maximum.
    assert validate_generated_sql(statement).count("DESC NULLS LAST") == rewritten


def test_a_comment_becomes_a_space_and_never_welds_two_tokens() -> None:
    # Replacing a comment with nothing would turn `1/*x*/AS` into `1AS`.
    assert "1 AS a" in validate_generated_sql("SELECT 1/*x*/AS a FROM email_message")


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT id FROM email_message WHERE subject ILIKE '%unterminated",
        "SELECT id FROM email_message /*unterminated",
        'SELECT "unterminated FROM email_message',
        "SELECT $tag$unterminated FROM email_message",
    ],
)
def test_an_unterminated_construct_is_refused_not_masked(statement: str) -> None:
    # Masking to end-of-input would hide whatever follows — including a second statement.
    with pytest.raises(ToolExecutionError):
        validate_generated_sql(statement)
