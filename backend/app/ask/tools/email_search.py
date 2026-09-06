"""
Role: search_emails + count_emails — the message-retrieval pair. search_emails returns the
      ranked page inside a completeness envelope (total, date_span, correspondence arc,
      full listing when it fits); count_emails answers "how many" over the identical filters.
Used by: app.ask.tools.shared_core (registry assembly).
Depends on: app.ask.tools.email_filters, app.ask.tools.tool_helpers, app.ask.tools.registry.
Key invariants:
  - ENVELOPE ORDER IS A CONTRACT: control/anchor fields FIRST, the bulky `results` page LAST,
    so the runner's payload budget can only ever cost snippet rows — never date_span,
    listing_complete or the notes the tool description tells the model to rely on (R2/N4).
  - Ranking is NULL-safe by construction: a NULL subject can never outrank a real hit (N3).
  - Never claim completeness the filters did not deliver: listing_complete is false above the
    listing cap and on zero matches, where an absence note explains what absence means.
  - sent_split is MAILBOX-relative, never party attribution — it ships with its own note (R4).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ask.exceptions import ToolExecutionError
from app.ask.tools.email_filters import (
    _EMAIL_FILTERS,
    _MAX_PARTY_GROUPS,
    _email_filter_params,
    _query_terms,
)
from app.ask.tools.registry import ToolSpec
from app.ask.tools.tool_helpers import (
    _LIMIT_PARAM,
    _MAX_LIMIT,
    _SNIPPET_CHARS,
    _clamp_limit,
    redact_uuids,
)


async def _search_emails(session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
    """Search messages by keyword variants and/or participant and/or date window, newest first.

    When a participant-style filter is active and the arc has more than two messages, the
    envelope also carries `sent_split` (inbound/outbound message counts over the same filters)
    and `span_boundary_emails` (the 3 earliest + 3 latest matches) — the correspondence-arc
    signal a best-match/newest-first page hides for 'between A and B' / 'how it started' facts.
    """
    params = _email_filter_params(args)
    groups_active = any(params[f"pa_g{i}_count"] for i in range(1, _MAX_PARTY_GROUPS + 1))
    if (not params["terms_count"] and not params["participants_count"]
            and not groups_active and not params["date_from"]):
        raise ToolExecutionError(
            "Provide at least one of: queries, participants, participants_all, date_from.")
    # Rank expression: NULL-safe by construction (M-49 class hazard in hand-written SQL —
    # `NULL ILIKE ANY(...)` is NULL and Postgres sorts NULLs FIRST under DESC, so subject-less
    # rows would outrank every real hit). coalesce pins NULL subjects to false, and the
    # terms_count gate pins the whole expression false on participant-only searches, where
    # term_likes is the [''] placeholder ('' ILIKE '' would otherwise rank empty subjects top).
    subject_hit_expr = (
        "(CAST(:terms_count AS int) > 0"
        " AND coalesce(m.subject, '') ILIKE ANY(CAST(:term_likes AS text[])))"
    )
    rows = await session.execute(
        text(
            f"""
            SELECT m.id, m.sent_at, m.from_address, m.from_name, m.subject,
                   m.direction, m.has_attachments,
                   left(coalesce(m.body_text, ''), {_SNIPPET_CHARS}) AS snippet,
                   {subject_hit_expr} AS subject_hit
            FROM email_message m
            WHERE {_EMAIL_FILTERS}
            ORDER BY {subject_hit_expr} DESC,
                     m.sent_at DESC NULLS LAST, m.id DESC
            LIMIT :limit
            """
        ),
        {**params, "limit": _clamp_limit(args)},
    )
    results = [dict(r) for r in rows.mappings()]
    total = int(
        (
            await session.execute(
                text(f"SELECT count(*) FROM email_message m WHERE {_EMAIL_FILTERS}"), params
            )
        ).scalar_one()
    )
    # Envelope field ORDER is a contract (cross-vendor R2/N4): control/anchor fields FIRST,
    # the bulky `results` page LAST, so budget truncation in the runner can only ever cost
    # snippet rows — never date_span/sent_split/listing_complete, which the tool description
    # tells the model to rely on. json.dumps preserves insertion order.
    envelope: dict[str, Any] = {"total_matches": total}
    # Results are ranked best-match-first, NOT by date — recency/oldest questions need
    # explicit anchors (measured: readers took the first hit as "the latest").
    if total > 1:
        span = await session.execute(
            text(
                f"""
                (SELECT 'earliest' AS which, m.id, m.sent_at, m.from_address,
                        left(coalesce(m.subject, ''), 80) AS subject
                 FROM email_message m WHERE {_EMAIL_FILTERS}
                 ORDER BY m.sent_at ASC NULLS LAST, m.id LIMIT 1)
                UNION ALL
                (SELECT 'latest', m.id, m.sent_at, m.from_address,
                        left(coalesce(m.subject, ''), 80)
                 FROM email_message m WHERE {_EMAIL_FILTERS}
                 ORDER BY m.sent_at DESC NULLS LAST, m.id DESC LIMIT 1)
                """
            ),
            params,
        )
        envelope["date_span"] = {r["which"]: dict(r) for r in span.mappings()}
    # Correspondence envelope (M-30b): for 'between A and B' / 'how did the relationship start
    # or end' questions, the best-match/newest-first page never shows the arc's ORIGIN. When a
    # participant filter is active, enrich with the mailbox-frame direction split and the true
    # span boundaries so an enumeration COVERS THE SPAN rather than a topical subset.
    # NB (cross-vendor R4): m.direction is derived at ingest as from_address == the SYNCED
    # MAILBOX's own address — it is NOT attribution between the queried parties (a colleague's
    # message to the counterparty is 'inbound' here). The envelope note states the frame.
    participant_filter_active = params["participants_count"] > 0 or groups_active
    if participant_filter_active and total > 2:
        split_rows = await session.execute(
            text(
                f"""
                SELECT m.direction, count(*) AS n
                FROM email_message m WHERE {_EMAIL_FILTERS}
                GROUP BY m.direction
                """
            ),
            params,
        )
        sent_split = {"inbound": 0, "outbound": 0, "unknown": 0}
        for r in split_rows.mappings():
            sent_split[r["direction"] or "unknown"] = int(r["n"])
        envelope["sent_split"] = sent_split
        envelope["sent_split_note"] = (
            "Counts are relative to the SYNCED MAILBOX (outbound = the mailbox account "
            "itself sent the message; everything else is inbound) — NOT attribution "
            "between the queried parties. Never report sent_split as 'who wrote to whom'; "
            "confirm authorship from the messages themselves."
        )
    # span_boundary_emails only above 6 matches: for 3–6 the two LIMIT-3 boundary queries
    # overlap and duplicate rows, and `listing` already enumerates EVERY match for total <= 50,
    # so the boundary is redundant there. Above 6, the two ends are disjoint and the ranked page
    # can no longer show the arc's origin — the point of the field. Note text (3 earliest + 3
    # latest of {total}) stays accurate because total is always >= 7 here. The latest arm
    # inverts the id tie-breaker (m.id DESC) so tied or NULL sent_at values still produce six
    # DISTINCT boundary rows instead of the same three ids twice (cross-vendor R5).
    if participant_filter_active and total > 6:
        boundary = await session.execute(
            text(
                f"""
                (SELECT 'earliest' AS which, m.id, m.sent_at::date AS sent_at,
                        m.from_address, left(coalesce(m.subject, ''), 80) AS subject
                 FROM email_message m WHERE {_EMAIL_FILTERS}
                 ORDER BY m.sent_at ASC NULLS LAST, m.id LIMIT 3)
                UNION ALL
                (SELECT 'latest' AS which, m.id, m.sent_at::date AS sent_at,
                        m.from_address, left(coalesce(m.subject, ''), 80) AS subject
                 FROM email_message m WHERE {_EMAIL_FILTERS}
                 ORDER BY m.sent_at DESC NULLS LAST, m.id DESC LIMIT 3)
                """
            ),
            params,
        )
        envelope["span_boundary_emails"] = {
            "note": (
                f"The 3 earliest and 3 latest of {total} matching messages — the BOUNDARIES of "
                "the correspondence arc. Best-match/newest-first pages never show where it "
                "STARTED; an enumeration must cover the whole span. State the total; never "
                "infer a relationship began or ended from a topical subset of the messages."
            ),
            "messages": [dict(r) for r in boundary.mappings()],
        }
    # Completeness contract for list/enumeration questions: when the full match set fits
    # under the cap, hand the reader EVERY row pre-formatted so "all" really means all.
    if 1 < total <= _MAX_LIMIT:
        full = await session.execute(
            text(
                f"""
                SELECT m.id, m.sent_at::date AS d, m.from_address,
                       left(coalesce(m.subject, ''), 80) AS subject
                FROM email_message m WHERE {_EMAIL_FILTERS}
                ORDER BY m.sent_at DESC NULLS LAST, m.id
                """
            ),
            params,
        )
        envelope["listing_complete"] = True
        envelope["listing"] = [
            f"{r['d']} · {r['from_address']} · {r['subject']} · [id: {r['id']}]"
            for r in full.mappings()
        ]
    else:
        # A 0-match result carries no enumeration to be complete: claiming completeness there
        # contradicts the honesty note emitted right below it. total == 1 IS fully described
        # by the single row in `results`, so that one stays true.
        envelope["listing_complete"] = total == 1
        if total > _MAX_LIMIT:
            envelope["listing_note"] = (
                f"{total} matches exceed the {_MAX_LIMIT}-row listing cap — this result is "
                "NOT a complete enumeration; narrow the filters and say so if asked for 'all'."
            )
        elif total == 0:
            # An over-restricted filter must never read as an authoritative empty set
            # (cross-vendor R1): the commonest cause is alias misuse in participants_all.
            envelope["listing_note"] = (
                "0 matches for THESE filters — absence of matches, not verified absence of "
                "the fact. participants_all requires EVERY entry on each message: one "
                "party's aliases belong INSIDE a single entry (as an array), never as "
                "separate entries. Retry with corrected filters and translated/"
                "transliterated variants before concluding no data exists."
            )
    if params["terms_count"]:
        envelope["per_term_matches"] = await _per_term_counts(session, args)
    if total > len(results):
        # No row count here: the runner may drop `results` rows to fit the payload budget,
        # and a hint stating a count the model can no longer see is worse than no count.
        envelope["hint"] = (
            f"This is one page of {total} matches (subject hits first, then newest). "
            "Narrow with date_from/date_to, participant, or more specific terms to reach "
            "the rest."
        )
    if not results and params["terms_count"]:
        envelope["hint"] = (
            "No matches for these terms. The archive content may be in another language — "
            "retry with translated keywords and transliterated names before concluding "
            "no data exists."
        )
    # ASK-02 F7: the language warning used to fire ONLY on zero results, so a partial
    # single-language hit silently masked the other language's (often dominant) share.
    # Always-on, question-blind: if every supplied term is single-script, say so.
    terms = _query_terms(args)
    if terms and results:
        has_cyrillic = any(any("Ѐ" <= ch <= "ӿ" for ch in t) for t in terms)
        has_latin = any(any("a" <= ch.lower() <= "z" for ch in t) for t in terms)
        if has_cyrillic != has_latin:
            missing = "the Latin script" if has_cyrillic else "the Cyrillic script"
            envelope["language_coverage"] = (
                f"All search terms are single-script; the archive may contain other "
                f"scripts/languages. These counts do NOT cover {missing} spellings — add "
                f"translated/transliterated variants to queries[] for the true total."
            )
    # The bulky snippet page goes LAST (see the envelope-order note above): if the runner's
    # payload budget must cut anything, it cuts snippet rows, never the control fields.
    envelope["results"] = results
    return envelope


async def _per_term_counts(session: AsyncSession, args: dict[str, Any]) -> dict[str, int]:
    """Per-variant match counts (same non-keyword filters) — which term actually hits.

    The keys ECHO the caller's own search terms, so they are redacted of uuids first: an
    observation is the evidence base, and a model that searched for an id it invented could
    otherwise read that id straight back out as something a tool returned.
    """
    counts: dict[str, int] = {}
    for term in _query_terms(args):
        single = _email_filter_params({**args, "queries": [term], "query": None})
        counts[redact_uuids(term)] = int(
            (
                await session.execute(
                    text(f"SELECT count(*) FROM email_message m WHERE {_EMAIL_FILTERS}"), single
                )
            ).scalar_one()
        )
    return counts


async def _count_emails(session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
    """Count messages matching the same filters as search_emails (never hand-count rows)."""
    params = _email_filter_params(args)
    result = await session.execute(
        text(f"SELECT count(*) AS matches FROM email_message m WHERE {_EMAIL_FILTERS}"),
        params,
    )
    envelope: dict[str, Any] = {"matches": int(result.scalar_one())}
    if params["terms_count"] > 1:
        envelope["per_term_matches"] = await _per_term_counts(session, args)
    return envelope



SEARCH_EMAILS_SPEC = ToolSpec(
    name="search_emails",
    description=(
        "Search email messages by keyword variants (subject+body), participant "
        "(sender or recipient name/address), and/or date window. ALWAYS pass "
        "several keyword variants together — the original term plus its "
        "translations into the archive's likely languages and transliterations "
        "of names — results match ANY variant. Results are ranked best-match "
        "FIRST, not by date — for 'latest/earliest/last communication' facts use "
        "the date_span field (the true earliest and latest matching messages), "
        "never the first listed result. For 'list / show all / find all' "
        "questions: the listing field enumerates EVERY match when "
        "listing_complete is true — your answer must include ALL its rows, not "
        "a sample; if listing_complete is false, say the enumeration is "
        "partial. If a `truncated` marker appears in the result, rows were "
        "dropped to fit the payload budget — the totals still describe the "
        "FULL match set. Use get_email for full messages. "
        "NOTE: documents and agreements often live in threads whose subject "
        "does not name them — when looking for a specific document or exchange, "
        "also search the counterparty's thread subjects and open the messages "
        "before concluding it does not exist. This archive may be multilingual: "
        "ALWAYS pair your search terms with your own translations into the "
        "archive's likely languages and transliterations of names — generate the "
        "translations yourself; never assume one language covers the corpus. "
        "For 'correspondence BETWEEN A and B' or 'how did the relationship "
        "start/end' questions (a participant filter set), read "
        "span_boundary_emails (the true first and last messages of the "
        "exchange) — never read the arc off the ranked page. sent_split counts "
        "are mailbox-relative volumes (see sent_split_note), NOT who-wrote-to-"
        "whom — verify authorship from the messages themselves."
    ),
    parameters={
        "type": "object",
        "properties": {
            "queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Up to 5 keyword variants (term + translations + "
                    "transliterations); a message matches if ANY variant matches."
                ),
            },
            "participants_all": {
                "type": "array",
                "maxItems": 4,
                "items": {
                    "anyOf": [
                        {"type": "string"},
                        {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "maxItems": 8,
                        },
                    ]
                },
                "description": (
                    "Up to 4 parties that must ALL appear on each message "
                    "(sender or recipient) — use for 'correspondence BETWEEN "
                    "A and B'. Each entry is ONE party: a name/address "
                    "fragment, or an ARRAY of that party's alias addresses/"
                    "names (the party matches if ANY alias appears), e.g. "
                    "[[\"a.old@x.com\", \"a.new@y.com\"], \"partner.com\"]."
                ),
            },
            "participants": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Names/addresses/domains of senders or recipients — pass "
                    "ALL of a person's addresses (see find_person) or a whole "
                    "domain like 'acme.com' to enumerate a full relationship; "
                    "matches ANY entry."
                ),
            },
            "date_from": {"type": "string", "description": "YYYY-MM-DD inclusive."},
            "date_to": {"type": "string", "description": "YYYY-MM-DD inclusive."},
            "limit": _LIMIT_PARAM,
        },
        "required": [],
    },
    executor=_search_emails,
)

COUNT_EMAILS_SPEC = ToolSpec(
    name="count_emails",
    description=(
        "Count email messages matching the same filters as search_emails "
        "(keyword variants, participant, date window). Use this for any "
        "'how many' question instead of counting search results yourself."
    ),
    parameters={
        "type": "object",
        "properties": {
            "queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Up to 5 keyword variants (term + translations + "
                    "transliterations); a message matches if ANY variant matches."
                ),
            },
            "participants_all": {
                "type": "array",
                "maxItems": 4,
                "items": {
                    "anyOf": [
                        {"type": "string"},
                        {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "maxItems": 8,
                        },
                    ]
                },
                "description": (
                    "Up to 4 parties that must ALL appear on each message "
                    "(sender or recipient) — use for 'correspondence BETWEEN "
                    "A and B'. Each entry is ONE party: a name/address "
                    "fragment, or an ARRAY of that party's alias addresses/"
                    "names (the party matches if ANY alias appears), e.g. "
                    "[[\"a.old@x.com\", \"a.new@y.com\"], \"partner.com\"]."
                ),
            },
            "participants": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Names/addresses/domains of senders or recipients — pass "
                    "ALL of a person's addresses (see find_person) or a whole "
                    "domain like 'acme.com' to enumerate a full relationship; "
                    "matches ANY entry."
                ),
            },
            "date_from": {"type": "string", "description": "YYYY-MM-DD inclusive."},
            "date_to": {"type": "string", "description": "YYYY-MM-DD inclusive."},
        },
        "required": [],
    },
    executor=_count_emails,
)
