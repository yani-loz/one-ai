"""
Role: The answer-PARSING primitives the grader is built on — the regexes and extractors that
      turn a free-text model answer into the numbers, dates, entity mentions and sentences a
      typed gold rule can be checked against. Parsing only: this module decides what an answer
      SAYS, never whether that is correct.
Used by: scripts.ask_loop.grade (the typed grading rules), scripts.ask_loop.conformance (the
      grader's own regression pins, which exercise these extractors directly).
Depends on: standard library only — grading must be re-computable offline, with no DB and no
      network, so a stored run can always be re-graded.
Key invariants:
  - Every strip performed here has a MEASURED provenance in its comment: each one exists
    because a real stored run passed or failed for the wrong reason, and each is pinned by a
    case in the conformance suite. Do not remove one without removing its pin and saying why.
  - _UUID_RE is the single definition of "an id" for the whole grader. What is stripped from a
    number extraction and what counts as a citation must never be two different regexes — they
    drifted once, and a citation's digit groups were read as the answer's count.
  - Extraction is polarity-BLIND. `_match_entity` answers "is this name asserted here as a
    word", nothing more; deciding whether the surrounding sentence affirms or disclaims it is
    the grading tier's job.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
_CITED_RE = re.compile(r"\[id:\s*([0-9a-f-]{36})\s*\]", re.IGNORECASE)
_NUMBER_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:[,.]\d{3})*|\d+)(?![\w%])")
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_TEXT_DATE_RE = re.compile(
    r"\b(\d{1,2})\.?\s*(January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\.?,?\s*(\d{4})\b",
    re.IGNORECASE,
)
# Month-first English dates ("May 26, 2026") — verifier MUT11b finding: parsing only
# day-first silently failed correct parent answers on format, inflating a flip.
_TEXT_DATE_MDY_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\.?\s+"
    r"(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})\b",
    re.IGNORECASE,
)
_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}
# A date written in PROSE, consumed from the month name through the year — "June 2026",
# "June 4, 2026", even "June 4-4, 2026". Counts bind to the FIRST number of the headline
# claim, so an unstripped date makes the same answer pass or fail by FORMAT alone: "from
# June 2026, I can identify 8 addresses" bound the count to 2026 while the ISO-dated wording
# of the same sentence bound it correctly to 8. Measured on real stored runs.
_PROSE_DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|"
    r"December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\.?[\s\d,\-–—]*(19|20)\d{2}\b",
    re.IGNORECASE,
)
# A BARE year in date position — "In 2026 the archive holds 2,962 threads". Only stripped
# after a time preposition, so a count that happens to be 2026 is left alone. Measured both
# ways on real golds: it failed a correct 2,962 answer, and passed a wrong 118 answer whose
# ±560 window happened to swallow the year.
_PROSE_YEAR_RE = re.compile(
    r"\b(in|from|since|during|by|for|of|as of|throughout)\s+(19|20)\d{2}\b", re.IGNORECASE
)
# The numeric-zero alternative carries a lookbehind (N6): without it '10 emails'/'20 messages'
# contain '0 emails'/'0 messages' and a data-asserting answer grades as an honest refusal.
_NO_DATA_PATTERNS = re.compile(
    # One optional qualifier between "no" and the noun: real answers say "no PDF attachments",
    # "no relevant emails", "no such messages" — all of which are no-data statements.
    r"no (\w+ )?(data|emails?|messages?|records?|results?|information|matches"
    r"|attachments?|documents?)"
    r"|not (found|contain|present|available)|nothing (was )?found|does not (contain|exist)"
    r"|couldn'?t find|could not find|there (are|is|were|was) no|archive (contains|has) no"
    r"|(?<![\d,.])0 (emails?|messages?|results?|matches)"
    r"|\bzero (emails?|messages?|results?|matches)"
    r"|няма (данни|имейли|съобщения|резултати)|не (са|е) (открити|намерен)",
    re.IGNORECASE,
)
# A POSITIVE quantity of corpus objects. Honesty on a no_data gold is the absence of a data
# claim, not the presence of a negative phrase: an incidental negative clause ("…but no
# attachments were included", "…the invoice was not found among them") otherwise auto-passed
# answers that assert the archive holds exactly what the gold says it does not.
_DATA_ASSERTION_RE = re.compile(
    r"\b([1-9]\d*)\s+(emails?|messages?|results?|matches|attachments?|documents?|threads?"
    r"|records?|имейла?|имейли|съобщени[ея]|резултата?|резултати|документа?|документи)\b",
    re.IGNORECASE,
)


_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_LIST_INDEX_RE = re.compile(r"(?m)^\s{0,4}\d{1,3}\.(?=\s)")


def _extract_numbers(answer: str) -> list[int]:
    """Standalone integers in the answer — after stripping contexts that leak digits.

    Verifier-confirmed false-pass sources removed BEFORE extraction: CITATIONS (the system
    prompt mandates `[id: <uuid>]` on every factual statement, and an all-digit uuid group is
    a number — `"…7 PDFs [id: 550e8400-0000-41d4-…]"` yielded [7, 0, 446655440000] and passed
    a `value: 0` gold), ISO dates (2026-07-04 → 2026/07/04), clock times (16:31:55 → 55), and
    markdown list indices ("5. Something"). Conformance suite pins each case.

    The uuid strip uses this module's own _UUID_RE, so what is REMOVED here and what counts as
    a citation elsewhere can never drift apart.
    """
    cleaned = _UUID_RE.sub(" ", answer)
    cleaned = _ISO_DATE_RE.sub(" ", cleaned)
    cleaned = _PROSE_DATE_RE.sub(" ", cleaned)
    cleaned = _PROSE_YEAR_RE.sub(" ", cleaned)
    cleaned = _TIME_RE.sub(" ", cleaned)
    cleaned = _LIST_INDEX_RE.sub(" ", cleaned)
    values = []
    for raw in _NUMBER_RE.findall(cleaned):
        try:
            values.append(int(raw.replace(",", "").replace(".", "")))
        except ValueError:
            continue
    return values


def _extract_dates(answer: str) -> list[date]:
    """All ISO and 'DD Month YYYY' dates in the answer."""
    found = []
    for y, m, d in _ISO_DATE_RE.findall(answer):
        try:
            found.append(date(int(y), int(m), int(d)))
        except ValueError:
            continue
    for d, month_name, y in _TEXT_DATE_RE.findall(answer):
        month = _MONTHS.get(month_name.lower()[:3])
        if month:
            try:
                found.append(date(int(y), month, int(d)))
            except ValueError:
                continue
    for month_name, d, y in _TEXT_DATE_MDY_RE.findall(answer):
        month = _MONTHS.get(month_name.lower()[:3])
        if month:
            try:
                found.append(date(int(y), month, int(d)))
            except ValueError:
                continue
    return found


# An answer that disclaims the very thing it was asked for is not that thing — whatever
# numbers or names it happens to contain. Measured: explicit refusals were scoring as correct
# counts and as correct entity identifications because an in-tolerance number or the asked-about
# name appeared somewhere in the sentence that refused.
_INABILITY_RE = re.compile(
    r"\b(cannot|can'?t|could not|couldn'?t|unable to|not able to|do not have|don'?t have"
    r"|no exact|not possible to|failed to)\b",
    re.IGNORECASE,
)

_MIN_ALTERNATIVE_CHARS = 4


def _match_entity(answer: str, spec: dict[str, Any]) -> bool:
    """True if the answer names this entity — as a WORD, not as a substring.

    Long names match case-insensitively as whole words. SHORT names (under
    _MIN_ALTERNATIVE_CHARS) must match case-SENSITIVELY, which is what separates the acronym
    an answer really used from an accidental fragment: 'GBS' and 'IBM' count, while a
    three-letter lowercase run inside another word does not. A blanket length floor was worse
    than the problem it solved — it silently scored the corpus's dominant counterparty
    (GBS / ГБС) and IBM as not-recalled across a dozen golds, because their only long-form
    names are 'Glavbulgarstroy' and domains.

    Substring matching is excluded outright: a gold alternative 'Beyond' was satisfied by
    "anything beyond that is not in the archive". Polarity is the caller's job — an answer
    that disclaims is routed to the critic — so this function answers only "is the name
    asserted here as a word".
    """
    answer_lower = answer.lower()
    names = [spec.get("canonical", "")] + list(spec.get("alternatives") or [])
    for name in names:
        token = str(name or "").strip()
        if not token:
            continue
        haystack, needle = (
            (answer, token)
            if len(token) < _MIN_ALTERNATIVE_CHARS
            else (answer_lower, token.lower())
        )
        if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack):
            return True
    return False


def _sentences(answer: str) -> list[str]:
    """Split an answer into sentences for claim-scoped checks."""
    return re.split(r"(?<=[.!?])\s+", answer.strip())
