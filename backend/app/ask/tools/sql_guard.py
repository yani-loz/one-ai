"""
Role: The guarded-SQL validator (FIX_BEFORE_PROD PF-FBP-8) — every model-generated SQL
      statement passes through validate_generated_sql() before touching the reader session.
      Fail-closed: anything not provably a single plain SELECT is rejected.
Used by: app.ask.tools.sql_tool (the query_database executor); any future SQL hatch.
Depends on: standard library only (auditable in isolation).
Key invariants:
  - DENY set_config and friends: the reader role is SELECT-only, but set_config() is callable
    from a SELECT and could rewrite the person/org GUCs to widen visibility — the documented
    PF-FBP-8 hazard this module exists to close.
  - LITERAL-AWARE (cross-vendor R7/N1): one character-level lexer recognizes '…' ('' doubling),
    E'…' (backslash escapes), $tag$…$tag$ dollar quoting, "…" identifiers, whole identifier
    runs (letters/digits/_/$ — so `a$$b$$` is ONE identifier and can never open a dollar
    quote), and (nested) /* */ plus -- comments. A search term like '%update%' is data, never
    a token, and literal content is never edited.
  - THREE position-aligned views, and the SCAN CHOICE IS SECURITY-CRITICAL: `masked` (string
    literals AND quoted-identifier bodies masked) drives the DESC rewrite, the ';' check and
    the LIMIT probe; `code` (only STRING literals masked — quoted identifiers kept verbatim)
    drives the forbidden-token and SELECT INTO scans, because `"set_config"(…)` is the SAME
    function as set_config and must never hide behind quoting (that regression shipped once).
  - FAIL-CLOSED LEXING: an unterminated literal, dollar quote, or block comment is REJECTED,
    never masked to end-of-input — masking to the end would hide set_config, a ';' and every
    DML keyword behind one stray quote. Unicode-escape syntax (U&'…' / U&"…") is refused
    outright: the SERVER decodes those escapes, so no name-based scan can see what the
    identifier really is (U&"\\0073et_config" is set_config — demonstrated, not theorized).
  - NOT THE ONLY LINE OF DEFENCE. This is a denylist over a language the server parses, so
    treat every acceptance as provisional: callers execute generated SQL through
    app.ask.tools.sql_execution.execute_guarded_sql, which re-checks the tenant/person GUCs
    afterwards and fails the call if the statement moved them.
  - Single statement, SELECT/WITH only, no SELECT INTO, no statement-separating semicolons.
  - Every bare DESC is normalized to DESC NULLS LAST before execution (M-49): PostgreSQL
    sorts NULLs FIRST under DESC, so an un-normalized DESC combined with the appended LIMIT
    can serve a NULL-valued row as the maximum. An explicit NULLS clause is left as written.
  - A LIMIT is appended when absent (payload/token discipline; never widens semantics).
"""

from __future__ import annotations

import re
from typing import NamedTuple

from app.ask.exceptions import ToolExecutionError

_MAX_SQL_CHARS = 4000
_DEFAULT_LIMIT = 50

# Function calls / statements that must never appear AS CODE in reader-plane generated SQL.
# The scan runs on the `code` view: STRING literals are masked (so these words are free to
# appear inside search terms like '%update%'), but quoted identifiers are NOT — `"set_config"`
# is the same function as set_config, so the quoted form must still be rejected. A legitimate
# column named "update" would be over-rejected; over-rejection falls through to the agent,
# under-rejection widens visibility.
_FORBIDDEN = re.compile(
    r"\b(set_config|setseed|pg_sleep|pg_terminate_backend|pg_cancel_backend|pg_reload_conf"
    r"|pg_read_file|pg_write_file|pg_ls_dir|lo_import|lo_export|dblink|copy|grant|revoke"
    r"|insert|update|delete|truncate|create|alter|drop|vacuum|reindex|cluster|listen"
    r"|notify|prepare|execute|deallocate|do|call|merge|refresh|security|lock)\b",
    re.IGNORECASE,
)
_SELECT_INTO = re.compile(r"\bselect\b[^;]*?\binto\b", re.IGNORECASE | re.DOTALL)
# An explicit NULLS FIRST/LAST already following a DESC token — such a sort is left as written.
_NULLS_CLAUSE = re.compile(r"\s*\bNULLS\b", re.IGNORECASE)
# Opener of a PostgreSQL dollar-quoted literal: $$ or $tag$ (tag = identifier-shaped).
_DOLLAR_TAG = re.compile(r"\$[A-Za-z_][A-Za-z_0-9]*\$|\$\$")
# A whole PostgreSQL identifier run. '$' is an identifier CONTINUATION character, so `a$$b$$`
# lexes as one identifier (PostgreSQL's longest-match rule) — consuming the run as a unit is
# what stops a glued '$' from opening a phantom dollar quote that masks the rest of the
# statement (and with it any set_config, ';' or DML keyword).
_IDENT_RUN = re.compile(r"[A-Za-z_-￿][A-Za-z0-9_$-￿]*")


class LexedSql(NamedTuple):
    """Three position-aligned, equal-length views of one statement, plus keyword token spans.

    stripped:   executable text (comments replaced by a space, every literal intact).
    masked:     string literals AND quoted-identifier bodies overwritten with 'x'.
    code:       string literals masked, quoted identifiers kept VERBATIM — the view the
                forbidden-token scan must use (`"set_config"` is still set_config).
    desc_spans: (start, end) of every STANDALONE `desc` keyword token. Token spans, not a
                regex over text: `\\bDESC\\b` also matches inside the single identifier
                `money$$desc$$` (regex treats '$' as a boundary, PostgreSQL does not), and
                rewriting there corrupts a column name.
    has_limit:  a standalone `limit` keyword token exists (same token-not-regex reasoning).
    """

    stripped: str
    masked: str
    code: str
    desc_spans: tuple[tuple[int, int], ...]
    has_limit: bool


def _is_word_char(ch: str) -> bool:
    """True for identifier-continuation characters (used to reject E'/$-quote false starts)."""
    return bool(ch) and (ch.isalnum() or ch in "_$")


def _mask_span(raw: str) -> str:
    """Length-preserving mask of one literal/identifier span.

    Every character except the outermost delimiters becomes 'x' — including a dollar-quote's
    TAG letters, so a `$desc$…$desc$` literal can never present a DESC token to the rewrite,
    and a `$update$` tag can never trip the forbidden scan.
    """
    if len(raw) <= 2:
        return "x" * len(raw)
    return raw[0] + "x" * (len(raw) - 2) + raw[-1]


def _lex_sql(sql: str) -> LexedSql:
    """One literal-aware pass over the statement; returns the three aligned views.

    Comments (line + nested block) collapse to a single space in all three views; every
    literal survives byte-identical in `stripped`. Token regexes run on `masked`/`code`;
    because all three strings are position-aligned, a match span found on either mask
    splices into `stripped` exactly.

    Postgres-faithful edges: '' / "" doubling; backslash escapes ONLY in E-strings; an
    identifier run is consumed whole (so an embedded '$' never opens a dollar quote); an
    `e` glued to a preceding identifier character never opens an E-string.

    Raises:
        ToolExecutionError: unterminated string, dollar quote, quoted identifier, or block
            comment — fail-closed, because masking to end-of-input would hide real code.
    """
    stripped: list[str] = []
    masked: list[str] = []
    code: list[str] = []
    desc_spans: list[tuple[int, int]] = []
    has_limit = False
    # Only a LIMIT at paren depth 0 bounds the RESULT. "a LIMIT token exists somewhere" is not
    # the same claim, and treating it as one meant an inner LIMIT suppressed the appended cap:
    # `SELECT a.id FROM email_message a, email_message b, (SELECT id FROM person LIMIT 1) z`
    # was executed uncapped, and SQLAlchemy buffers the WHOLE result before max_rows slices 50
    # rows off it. On the dev corpus that is a ~35M-row cartesian product in process memory.
    paren_depth = 0
    out = 0  # length already emitted — the position of the NEXT span in all three views

    def emit_literal(raw: str, *, is_identifier: bool) -> None:
        """Append one lexed span to all three views (identifiers stay readable in `code`)."""
        nonlocal out
        stripped.append(raw)
        masked.append(_mask_span(raw))
        code.append(raw if is_identifier else _mask_span(raw))
        out += len(raw)

    def emit_plain(raw: str) -> None:
        """Append text that is identical in all three views (whitespace, operators, keywords)."""
        nonlocal out
        stripped.append(raw)
        masked.append(raw)
        code.append(raw)
        out += len(raw)

    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        prev = sql[i - 1] if i else ""
        if ch == "-" and nxt == "-":  # line comment → one space (newline itself kept)
            eol = sql.find("\n", i)
            i = n if eol < 0 else eol
            emit_plain(" ")
            continue
        if ch == "/" and nxt == "*":  # block comment — PostgreSQL nests these
            depth, i = 1, i + 2
            while i < n and depth:
                if sql.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif sql.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            if depth:
                raise ToolExecutionError("Unterminated block comment in generated SQL.")
            emit_plain(" ")
            continue
        is_uescape_start = (
            ch in "uU"
            and nxt == "&"
            and sql[i + 2 : i + 3] in ("'", '"')
            and not _is_word_char(prev)
        )
        if is_uescape_start:
            # U&'…' / U&"…" are PostgreSQL UNICODE-ESCAPE literals and identifiers: the server
            # DECODES the escapes, so U&"\\0073et_config" IS the identifier set_config while the
            # raw text a token scan sees is not. Any name-based denylist is blind here by
            # construction, so the syntax itself is refused — the SQL specialist never emits it,
            # and over-rejection merely falls through to the agent.
            raise ToolExecutionError(
                "Unicode-escape syntax (U&'…' / U&\"…\") is not allowed in generated SQL — "
                "write plain literals and identifiers."
            )
        is_estring_start = ch in "eE" and nxt == "'" and not _is_word_char(prev)
        if ch == "'" or is_estring_start:
            j, closed = i + (2 if is_estring_start else 1), False
            while j < n:
                if is_estring_start and sql[j] == "\\":
                    j += 2  # backslash escape (E-strings only)
                    continue
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":  # '' doubling
                        j += 2
                        continue
                    j, closed = j + 1, True
                    break
                j += 1
            if not closed:
                raise ToolExecutionError("Unterminated string literal in generated SQL.")
            emit_literal(sql[i:j], is_identifier=False)
            i = j
            continue
        if ch == '"':
            j, closed = i + 1, False
            while j < n:
                if sql[j] == '"':
                    if j + 1 < n and sql[j + 1] == '"':  # "" doubling
                        j += 2
                        continue
                    j, closed = j + 1, True
                    break
                j += 1
            if not closed:
                raise ToolExecutionError("Unterminated quoted identifier in generated SQL.")
            emit_literal(sql[i:j], is_identifier=True)
            i = j
            continue
        if ch in "()":
            paren_depth += 1 if ch == "(" else -1
        ident = _IDENT_RUN.match(sql, i)
        if ident:  # whole identifier run, BEFORE the '$' branch (see _IDENT_RUN)
            raw = ident.group(0)
            folded = raw.lower()
            if folded == "desc":
                desc_spans.append((out, out + len(raw)))
            elif folded == "limit" and paren_depth == 0:
                has_limit = True
            emit_plain(raw)
            i = ident.end()
            continue
        if ch == "$":
            tag_match = _DOLLAR_TAG.match(sql, i)
            if tag_match:
                tag = tag_match.group(0)
                end = sql.find(tag, tag_match.end())
                if end < 0:
                    raise ToolExecutionError(
                        "Unterminated dollar-quoted literal in generated SQL."
                    )
                j = end + len(tag)
                emit_literal(sql[i:j], is_identifier=False)
                i = j
                continue
        emit_plain(ch)
        i += 1
    return LexedSql(
        "".join(stripped), "".join(masked), "".join(code), tuple(desc_spans), has_limit
    )


def validate_generated_sql(sql: str) -> str:
    """Return the safe, executable form of a generated statement — or raise.

    Contract: comments stripped, trailing semicolons removed, single statement enforced,
    SELECT/WITH-only, forbidden tokens rejected (on the `code` view — search-term literals
    never trip the scan, but a quoted `"set_config"` still does), every bare DESC normalized
    to DESC NULLS LAST (M-49), LIMIT appended when absent. The RETURNED string (never the
    input) is what the caller may execute.

    Raises:
        ToolExecutionError: with a model-repairable reason on any violation.
    """
    if not sql or not sql.strip():
        raise ToolExecutionError("Generated SQL is empty.")
    lexed = _lex_sql(sql)
    stripped, masked, code = lexed.stripped, lexed.masked, lexed.code
    # Trim leading whitespace + trailing whitespace/semicolons with ONE set of slice bounds —
    # the views are the same length, so the bounds apply to all three without desync.
    start = len(stripped) - len(stripped.lstrip())
    end = len(stripped.rstrip())
    while end > start and stripped[end - 1] in "; \t\n\r":
        end -= 1
    stripped, masked, code = stripped[start:end], masked[start:end], code[start:end]
    # Token spans were recorded against the untrimmed views — rebase them onto the slice.
    desc_spans = [
        (s - start, e - start) for s, e in lexed.desc_spans if s >= start and e <= end
    ]
    if not stripped:
        raise ToolExecutionError("Generated SQL is empty.")
    if len(stripped) > _MAX_SQL_CHARS:
        raise ToolExecutionError("Generated SQL exceeds the size limit.")
    if ";" in masked:  # a ';' inside a literal is data and stays masked — this finds real ones
        raise ToolExecutionError("Only a single SQL statement is allowed.")
    lowered = stripped.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise ToolExecutionError("Only SELECT statements are allowed.")
    if _SELECT_INTO.search(code):
        raise ToolExecutionError("SELECT INTO is not allowed.")
    # `code`, NOT `masked`: quoting a function name changes nothing at execution time, so
    # `"set_config"(…)` must be rejected exactly like the bare form (regression guard).
    forbidden = _FORBIDDEN.search(code)
    if forbidden:
        raise ToolExecutionError(
            f"Forbidden token {forbidden.group(0)!r} in generated SQL — plain read-only "
            "SELECT only."
        )
    # M-49: PostgreSQL sorts NULLs FIRST under DESC, and the guard always caps output with a
    # LIMIT, so a bare `ORDER BY col DESC` can serve a NULL-valued row as the maximum. A 7B
    # text-to-SQL model won't avoid this from a prompt note alone — normalize every bare DESC
    # to DESC NULLS LAST. Only STANDALONE desc TOKENS are rewritten (a '%desc%' literal, a
    # "desc" identifier and the `money$$desc$$` column name are all untouchable), and a DESC
    # that already carries an explicit NULLS clause is left as written.
    pieces: list[str] = []
    last = 0
    for span_start, span_end in desc_spans:
        if _NULLS_CLAUSE.match(stripped, span_end):
            continue
        pieces.append(stripped[last:span_start])
        pieces.append("DESC NULLS LAST")
        last = span_end
    pieces.append(stripped[last:])
    cleaned = "".join(pieces)
    if not lexed.has_limit:
        cleaned = f"{cleaned}\nLIMIT {_DEFAULT_LIMIT}"
    return cleaned
