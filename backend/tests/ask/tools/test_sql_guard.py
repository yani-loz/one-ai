"""
Role: Unit tests for the guarded-SQL validator (PF-FBP-8) — proves the fail-closed
      checks (SELECT-only, set_config denied, single statement) and the M-49 DESC NULLS
      LAST normalization that stops a NULL row being served as the maximum under DESC.
Used by: pytest (tests/ask/tools). Pure string transforms — no DB, no fixtures.
Depends on: app.ask.tools.sql_guard, app.ask.exceptions.
"""

from __future__ import annotations

import pytest

from app.ask.exceptions import ToolExecutionError
from app.ask.tools.sql_guard import validate_generated_sql
from app.ask.tools.tool_helpers import redact_uuids

# — M-49: bare DESC is normalized to DESC NULLS LAST —


def test_validate_bare_desc_rewrites_to_nulls_last() -> None:
    result = validate_generated_sql(
        "SELECT id FROM email_attachment ORDER BY size_bytes DESC LIMIT 5"
    )

    assert "ORDER BY size_bytes DESC NULLS LAST" in result
    assert "NULLS FIRST" not in result


def test_validate_lowercase_desc_rewrites_to_nulls_last() -> None:
    result = validate_generated_sql(
        "select id from email_message order by sent_at desc limit 5"
    )

    assert "sent_at DESC NULLS LAST" in result


def test_validate_multiple_desc_directions_all_rewritten() -> None:
    result = validate_generated_sql(
        "SELECT id FROM email_message ORDER BY sent_at DESC, subject DESC LIMIT 5"
    )

    assert result.count("DESC NULLS LAST") == 2


def test_validate_existing_nulls_last_left_untouched() -> None:
    result = validate_generated_sql(
        "SELECT id FROM email_attachment ORDER BY size_bytes DESC NULLS LAST LIMIT 5"
    )

    assert result.count("NULLS LAST") == 1
    assert "DESC NULLS LAST NULLS LAST" not in result


def test_validate_existing_nulls_first_left_untouched() -> None:
    result = validate_generated_sql(
        "SELECT id FROM email_attachment ORDER BY size_bytes DESC NULLS FIRST LIMIT 5"
    )

    assert "DESC NULLS FIRST" in result
    assert "NULLS LAST" not in result


def test_validate_asc_order_no_nulls_clause_added() -> None:
    result = validate_generated_sql(
        "SELECT id FROM email_message ORDER BY sent_at ASC LIMIT 5"
    )

    assert "NULLS" not in result


def test_validate_description_substring_not_mistaken_for_desc() -> None:
    result = validate_generated_sql("SELECT description FROM email_message LIMIT 5")

    assert "NULLS" not in result  # 'desc' inside 'description' is not word-bounded


def test_validate_desc_in_string_literal_preserved_while_order_by_rewritten() -> None:
    result = validate_generated_sql(
        "SELECT id FROM email_message WHERE subject ILIKE '%desc%' "
        "ORDER BY sent_at DESC LIMIT 5"
    )

    assert "ILIKE '%desc%'" in result  # the literal survives byte-identical
    assert "sent_at DESC NULLS LAST" in result  # the real ORDER BY is still normalized


def test_validate_quoted_desc_identifier_not_rewritten() -> None:
    result = validate_generated_sql('SELECT "desc" FROM email_message LIMIT 5')

    assert '"desc"' in result  # the quoted identifier is left untouched
    assert "NULLS" not in result


def test_validate_desc_rewrite_composes_with_limit_append() -> None:
    result = validate_generated_sql("SELECT id FROM email_message ORDER BY sent_at DESC")

    assert "DESC NULLS LAST" in result
    assert result.rstrip().endswith("LIMIT 50")
    assert result.index("DESC NULLS LAST") < result.index("LIMIT 50")


# — Literal awareness (cross-vendor R7/N1): tokens inside literals are DATA, not SQL —


def test_validate_forbidden_words_inside_literals_accepted() -> None:
    # Ordinary search terms must never trip the forbidden-token scan (N1: ~19 common
    # English words made query_database unusable for realistic questions).
    for term in ("%update%", "%security%", "%call%", "%create%", "%merge%", "%into%"):
        result = validate_generated_sql(
            f"SELECT id FROM email_message WHERE body_text ILIKE '{term}'"
        )

        assert f"ILIKE '{term}'" in result


def test_validate_forbidden_token_outside_literal_still_rejected() -> None:
    with pytest.raises(ToolExecutionError):
        validate_generated_sql("SELECT id FROM email_message WHERE update = 1")


def test_validate_dollar_quoted_literal_survives_desc_rewrite() -> None:
    result = validate_generated_sql("SELECT $$desc$$ FROM email_message")

    assert "$$desc$$" in result  # the literal is byte-identical, not 'DESC NULLS LAST'
    assert "NULLS" not in result


def test_validate_tagged_dollar_quote_and_desc_tag_survive() -> None:
    result = validate_generated_sql(
        "SELECT $desc$update$desc$ FROM email_message ORDER BY sent_at DESC"
    )

    assert "$desc$update$desc$" in result  # neither the tag nor the body is touched
    assert "sent_at DESC NULLS LAST" in result  # the real ORDER BY is still normalized


def test_validate_estring_backslash_quote_survives() -> None:
    result = validate_generated_sql(
        "SELECT id FROM email_message WHERE subject ILIKE E'a\\'desc\\'b' "
        "ORDER BY sent_at DESC"
    )

    assert "E'a\\'desc\\'b'" in result
    assert "sent_at DESC NULLS LAST" in result


def test_validate_double_dash_inside_literal_is_not_a_comment() -> None:
    result = validate_generated_sql(
        "SELECT id FROM email_message WHERE subject ILIKE '%a--b%' ORDER BY sent_at DESC"
    )

    assert "'%a--b%'" in result  # the literal survives; nothing after '--' was stripped
    assert "sent_at DESC NULLS LAST" in result


def test_validate_semicolon_inside_literal_is_single_statement() -> None:
    result = validate_generated_sql(
        "SELECT id FROM email_message WHERE subject ILIKE '%a;b%'"
    )

    assert "'%a;b%'" in result


def test_validate_comment_with_forbidden_word_still_stripped_and_accepted() -> None:
    result = validate_generated_sql(
        "SELECT id FROM email_message /* update these later */ ORDER BY sent_at DESC"
    )

    assert "update" not in result.lower()
    assert "sent_at DESC NULLS LAST" in result


def test_validate_nested_block_comment_fully_stripped() -> None:
    result = validate_generated_sql(
        "SELECT id FROM email_message /* outer /* inner */ still-comment */ LIMIT 5"
    )

    assert "comment" not in result
    assert result.startswith("SELECT id FROM email_message")


def test_validate_forbidden_hidden_after_estring_false_start_rejected() -> None:
    # An identifier glued to a quote must NOT open an E-string (that would mask real code
    # away from the forbidden scan) — the trailing set_config stays visible and rejected.
    with pytest.raises(ToolExecutionError):
        validate_generated_sql(
            "SELECT abce'a', set_config('app.current_org_id', 'x', true) FROM t"
        )


# — Masking must never hide CODE from the forbidden scan (guard-bypass regressions) —


@pytest.mark.parametrize(
    "statement",
    [
        # A quoted function name is the SAME function in PostgreSQL: quoting must not hide it.
        "SELECT \"set_config\"('app.current_person_id', 'x', true)",
        'SELECT "SET_CONFIG"(\'a\', \'b\', true)',
        'SELECT id, "pg_read_file"(\'/etc/passwd\') FROM email_message',
        # '$' is an identifier continuation char: `a$$b$$` is ONE identifier, not a dollar
        # quote. Lexing it as a quote masked the rest of the statement — set_config included.
        "SELECT 1 AS a$$b$$, set_config('app.current_person_id', 'x', false) FROM t",
        # …and the same phantom quote hid a second statement from the single-statement check.
        "SELECT a$$q$ FROM t; DROP TABLE email_message",
    ],
)
def test_validate_forbidden_token_cannot_hide_behind_quoting(statement: str) -> None:
    with pytest.raises(ToolExecutionError):
        validate_generated_sql(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT 1 /* unterminated",
        "SELECT id FROM t WHERE s ILIKE '%unterminated",
        'SELECT "unterminated FROM t',
        "SELECT $$unterminated FROM t",
    ],
)
def test_validate_unterminated_literal_rejected(statement: str) -> None:
    # Fail-closed: masking an unterminated literal to end-of-input would hide everything
    # after one stray quote from every check.
    with pytest.raises(ToolExecutionError):
        validate_generated_sql(statement)


def test_validate_dollar_in_identifier_survives_desc_rewrite() -> None:
    # `money$$desc$$` is one column name; only the real trailing DESC keyword is normalized.
    result = validate_generated_sql("SELECT money$$desc$$ FROM t ORDER BY x DESC")

    assert "money$$desc$$" in result
    assert result.count("DESC NULLS LAST") == 1


def test_redact_uuids_keeps_model_ids_out_of_the_echoed_sql() -> None:
    # The echoed statement is context, not evidence: an id the model itself supplied must not
    # come back as something a tool "returned" (citation grading scans the observation).
    redacted = redact_uuids(
        "SELECT id FROM email_message WHERE id = '44444444-4444-4444-4444-444444444444'"
    )

    assert "44444444-4444-4444-4444-444444444444" not in redacted
    assert "<uuid>" in redacted


@pytest.mark.parametrize(
    "echoed",
    [
        "x44444444-4444-4444-4444-444444444444",  # glued to a leading word character
        "44444444-4444-4444-4444-444444444444x",  # glued to a trailing one
        "id=44444444-4444-4444-4444-444444444444.",
        "44444444-4444-4444-4444-444444444444",
    ],
)
def test_redaction_matches_wherever_the_citation_grader_looks(echoed: str) -> None:
    # The grader extracts uuids with NO word boundary, so a redaction that requires one is
    # not a redaction: the uuid survives the echo and is harvested as tool evidence.
    assert "44444444-4444-4444-4444-444444444444" not in redact_uuids(echoed)


# — Existing fail-closed guard behaviors still hold —


def test_validate_non_select_statement_rejected() -> None:
    with pytest.raises(ToolExecutionError):
        validate_generated_sql("UPDATE email_message SET subject = 'x'")


def test_validate_set_config_call_rejected() -> None:
    with pytest.raises(ToolExecutionError):
        validate_generated_sql("SELECT set_config('app.person_id', 'x', false)")


def test_validate_multiple_statements_rejected() -> None:
    with pytest.raises(ToolExecutionError):
        validate_generated_sql("SELECT 1; SELECT 2")


def test_validate_empty_sql_rejected() -> None:
    with pytest.raises(ToolExecutionError):
        validate_generated_sql("   ")
