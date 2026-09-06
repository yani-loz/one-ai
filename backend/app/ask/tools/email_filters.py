"""
Role: The shared WHERE clause of every email query (keyword OR-terms, participant OR-terms,
      per-party alias AND-groups, date window) plus the argument parsing that feeds it —
      one filter semantic for search_emails and count_emails alike.
Used by: app.ask.tools.email_search (both executors + the per-term counter).
Depends on: app.ask.tools.tool_helpers, app.ask.exceptions.
Key invariants:
  - BCC RECIPIENTS ARE NOT MATCHABLE (PF-01 AC19). Participant filters skip kind='bcc' rows,
    because a filter that matches them is a membership oracle: total_matches would answer
    "was this address blind-copied?" without ever showing a row. Fails closed for everyone —
    the reader plane cannot prove who owns the stored copy.
  - NEVER silently drop a caller's term: an over-cap list RAISES. Dropping an AND-conjunct
    widens the match and an OR-variant narrows it, while the envelope keeps claiming
    completeness — a confidently wrong answer either way (cross-vendor R3/N14).
  - The party-group slots are STATIC (4 conjuncts, empty ones short-circuit on count=0), so
    the statement text never varies with input: one plan cache, no dynamic SQL assembly.
  - Every query interpolating _EMAIL_FILTERS must bind the params from _email_filter_params.
"""

from __future__ import annotations

from typing import Any

from app.ask.exceptions import ToolExecutionError
from app.ask.tools.tool_helpers import _like, _parse_iso_date

_MAX_QUERY_TERMS = 5
_MAX_PARTICIPANT_TERMS = 8
_MAX_PARTY_GROUPS = 4
_MAX_GROUP_ALIASES = 8


def _party_group_clause(i: int) -> str:
    """One participants_all conjunct: required party #i is PRESENT if ANY of its alias
    patterns matches the sender or any recipient (per-party OR groups, AND across parties —
    the (a_old OR a_new) AND (b_old OR b_new) shape a flat pattern list cannot express)."""
    return f"""
  AND (CAST(:pa_g{i}_count AS int) = 0
       OR coalesce(m.from_address, '') ILIKE ANY(CAST(:pa_g{i}_likes AS text[]))
       OR coalesce(m.from_name, '') ILIKE ANY(CAST(:pa_g{i}_likes AS text[]))
       OR EXISTS (SELECT 1 FROM email_recipient rg{i}
                  WHERE rg{i}.email_id = m.id AND rg{i}.org_id = m.org_id
                    AND rg{i}.kind IN ('to', 'cc')
                    AND (coalesce(rg{i}.address, '') ILIKE ANY(CAST(:pa_g{i}_likes AS text[]))
                         OR coalesce(rg{i}.name, '') ILIKE ANY(CAST(:pa_g{i}_likes AS text[])))))"""


# The party-group slots are STATIC (always 4 conjuncts; empty groups short-circuit on count=0)
# so the statement text never varies with input — same plan cache, no dynamic SQL assembly.
_EMAIL_FILTERS = (
    """
      (CAST(:terms_count AS int) = 0
       OR m.subject ILIKE ANY(CAST(:term_likes AS text[]))
       OR m.body_text ILIKE ANY(CAST(:term_likes AS text[])))
  AND (CAST(:participants_count AS int) = 0
       OR m.from_address ILIKE ANY(CAST(:participant_likes AS text[]))
       OR m.from_name ILIKE ANY(CAST(:participant_likes AS text[]))
       OR EXISTS (SELECT 1 FROM email_recipient r
                  WHERE r.email_id = m.id AND r.org_id = m.org_id
                    AND r.kind IN ('to', 'cc')
                    AND (r.address ILIKE ANY(CAST(:participant_likes AS text[]))
                         OR r.name ILIKE ANY(CAST(:participant_likes AS text[])))))"""
    + "".join(_party_group_clause(i) for i in range(1, _MAX_PARTY_GROUPS + 1))
    + """
  AND (CAST(:date_from AS date) IS NULL OR m.sent_at >= CAST(:date_from AS date))
  AND (CAST(:date_to AS date) IS NULL
       OR m.sent_at < CAST(:date_to AS date) + INTERVAL '1 day')
"""
)


def _query_terms(args: dict[str, Any]) -> list[str]:
    """Collect keyword variants from `queries` (list) or legacy `query` (string).

    Over-cap input RAISES rather than trimming: dropping an OR variant NARROWS the match set
    while `total_matches`/`listing_complete` keep claiming completeness, so the reader would
    be handed a confidently under-counted answer to a question it never asked.
    """
    raw = args.get("queries")
    if raw is None:
        raw = [args.get("query")] if args.get("query") else []
    if not isinstance(raw, list):
        raw = [raw]
    terms = [str(t).strip() for t in raw if t and str(t).strip()]
    if len(terms) > _MAX_QUERY_TERMS:
        raise ToolExecutionError(
            f"queries supports at most {_MAX_QUERY_TERMS} variants, got {len(terms)} — keep "
            "the most distinctive spellings, or run a second search for the rest."
        )
    return terms


def _participant_terms(args: dict[str, Any]) -> list[str]:
    """Collect participant variants from `participants` (list) or legacy `participant`.

    Same no-silent-drop rule as `_query_terms`: an over-cap list raises instead of trimming.
    """
    raw = args.get("participants")
    if raw is None:
        raw = [args.get("participant")] if args.get("participant") else []
    if not isinstance(raw, list):
        raw = [raw]
    terms = [str(t).strip() for t in raw if t and str(t).strip()]
    if len(terms) > _MAX_PARTICIPANT_TERMS:
        raise ToolExecutionError(
            f"participants supports at most {_MAX_PARTICIPANT_TERMS} entries, got "
            f"{len(terms)} — pass the person's distinctive addresses or their domain."
        )
    return terms


def _participants_all_groups(args: dict[str, Any]) -> list[list[str]]:
    """ASK-02 F2: `participants_all` — EVERY listed party must appear on the message
    (sender or recipient); the 'correspondence between A and B' semantic the OR filter
    could not express. Each entry is one PARTY: a plain string, or a list of that party's
    alias addresses/names (the party counts as present if ANY alias matches).

    Oversized input RAISES instead of being trimmed: participants_all conjuncts NARROW the
    match, so silently dropping one would widen the result and misreport the envelope's
    totals/listing as answering a requirement the caller never made (cross-vendor R3/N14).
    """
    raw = args.get("participants_all")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raw = [raw]
    groups: list[list[str]] = []
    for position, entry in enumerate(raw, start=1):
        aliases = entry if isinstance(entry, list) else [entry]
        cleaned = [str(a).strip() for a in aliases if a and str(a).strip()]
        if len(cleaned) > _MAX_GROUP_ALIASES:
            raise ToolExecutionError(
                f"participants_all: one party lists {len(cleaned)} aliases — at most "
                f"{_MAX_GROUP_ALIASES} are supported; keep the distinctive ones."
            )
        if not cleaned:
            # Dropping a blank entry would widen the AND — the same silent-widening direction
            # the cap check above exists to prevent, so it is an error, not a skip.
            raise ToolExecutionError(
                f"participants_all entry {position} is empty — every required party needs a "
                "name/address fragment (or an array of that party's aliases)."
            )
        groups.append(cleaned)
    if len(groups) > _MAX_PARTY_GROUPS:
        raise ToolExecutionError(
            f"participants_all supports at most {_MAX_PARTY_GROUPS} required parties, got "
            f"{len(groups)} — split the question, or move optional parties to participants "
            "(ANY-match)."
        )
    return groups


def _email_filter_params(args: dict[str, Any]) -> dict[str, Any]:
    """Shared param builder for search_emails/count_emails (identical filter semantics)."""
    terms = _query_terms(args)
    participants = _participant_terms(args)
    party_groups = _participants_all_groups(args)
    params: dict[str, Any] = {
        "terms_count": len(terms),
        "term_likes": [_like(t) for t in terms] or [""],
        "participants_count": len(participants),
        "participant_likes": [_like(t) for t in participants] or [""],
        "date_from": _parse_iso_date(args.get("date_from"), "date_from"),
        "date_to": _parse_iso_date(args.get("date_to"), "date_to"),
    }
    for i in range(1, _MAX_PARTY_GROUPS + 1):
        aliases = party_groups[i - 1] if i <= len(party_groups) else []
        params[f"pa_g{i}_count"] = len(aliases)
        params[f"pa_g{i}_likes"] = [_like(a) for a in aliases] or [""]
    return params

