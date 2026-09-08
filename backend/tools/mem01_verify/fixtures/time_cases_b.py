"""TIME battery, part B — comments and folding, leap-day arithmetic, malformed values.

Role:
    The second contiguous half of the TIME conformance battery. It carries ``TIME_CASES_B``: the
    comment / folding / whitespace variants, the leap-day arithmetic cases, and the values that
    are not an RFC 5322 ``date-time`` at all and must resolve to the ``malformed`` state.

Used by:
    ``tools.mem01_verify.fixtures.time_cases`` (the public aggregator named in contract section
    1.3, which concatenates part A and part B into ``TIME_CASES``).

Depends on:
    ``tools.mem01_verify.fixtures.time_cases_a`` for the ``TimeCase`` record type and the three
    builders — data only. Nothing else inside the project: a fixture module that imported a
    measured component could not honour contract R12.

Key invariants:
    - The A/B split is a file-size measure only (`.claude/rules/code-quality.md` A2) and carries
      no semantics. Part B is a contiguous SUFFIX of the battery: ``TIME_CASES_A +
      TIME_CASES_B`` reproduces the original record order exactly.
    - The battery's full invariant set is stated once, in ``time_cases``, and governs the records
      here. The calendar facts the leap-day cases rest on are stated in ``time_cases_a``, above
      ``TIME_CASES_A``.
    - **R12.** Every ``expected`` value was derived by hand from the RFC, never by running
      ``parse_date``, ``headers.py`` or any other measured component.
    - No gate imports ``TIME_CASES_B``; gates import ``TIME_CASES`` from ``time_cases``.
"""

from __future__ import annotations

from tools.mem01_verify.fixtures.time_cases_a import TimeCase, _case, _instant, _state

TIME_CASES_B: tuple[TimeCase, ...] = (
    # --- Comments, folding and whitespace variants --------------------------------------------
    _case(
        "time-041",
        "Mon, 12 Jan 2026 09:15:00 +0200 (EET)",
        _instant("2026-01-12T07:15:00Z"),
        "RFC 5322 3.2.2: a trailing comment is CFWS, semantically whitespace. The numeric "
        "zone, not the comment text, carries the offset.",
    ),
    _case(
        "time-042",
        "Wed, 15 Jul 2026 09:15:00 +0300 (Източноевропейско лятно време)",
        _instant("2026-07-15T06:15:00Z"),
        "Bulgarian UTF-8 comment: RFC 6532 3.2 extends ctext to non-ASCII UTF-8; the comment "
        "is still ignored and the numeric offset still decides.",
    ),
    _case(
        "time-043",
        "Mon, 12 Jan 2026 09:15:00 +0200 (Eastern European Time (winter))",
        _instant("2026-01-12T07:15:00Z"),
        "RFC 5322 3.2.2: comments nest; a nested comment must not truncate the parse.",
    ),
    _case(
        "time-044",
        "Mon, 12 Jan 2026 09:15:00 (обяд / midday) +0200",
        _instant("2026-01-12T07:15:00Z"),
        "Bilingual comment BETWEEN the time-of-day and the zone: obs-second is "
        "'[CFWS] 2DIGIT [CFWS]' (RFC 5322 4.3), so a comment is legal in that position too.",
    ),
    _case(
        "time-045",
        "Mon, 12 Jan 2026\r\n 09:15:00 +0200",
        _instant("2026-01-12T07:15:00Z"),
        "Folded header value: FWS is '[*WSP CRLF] 1*WSP' (RFC 5322 3.2.2), so the CRLF+SP is "
        "whitespace after unfolding, not a truncation point.",
    ),
    _case(
        "time-046",
        "Wed, 15 Jul 2026 09:15:00 +0300\r\n (лято / summer)",
        _instant("2026-07-15T06:15:00Z"),
        "Fold inside the trailing comment: unfolding must happen before comment stripping.",
    ),
    _case(
        "time-047",
        "  Mon,  12  Jan  2026  09:15:00  +0200  ",
        _instant("2026-01-12T07:15:00Z"),
        "Runs of WSP collapse into one FWS and leading/trailing CFWS is allowed (RFC 5322 3.3).",
    ),
    _case(
        "time-048",
        "Mon, 12 Jan 2026 09:15 +0200",
        _instant("2026-01-12T07:15:00Z"),
        'RFC 5322 3.3 time-of-day is \'hour ":" minute [ ":" second ]\' — the seconds field '
        "is optional and defaults to 00; omitting it is not a defect.",
    ),
    # --- Leap-day arithmetic -------------------------------------------------------------------
    _case(
        "time-049",
        "Thu, 29 Feb 2024 12:00:00 +0200",
        _instant("2024-02-29T10:00:00Z"),
        "29 February exists in 2024 (divisible by 4, not a century year) — a valid instant.",
    ),
    _case(
        "time-050",
        "Tue, 29 Feb 2000 12:00:00 +0000",
        _instant("2000-02-29T12:00:00Z"),
        "Century rule: 2000 IS a leap year because it is divisible by 400.",
    ),
    _case(
        "time-051",
        "29 Feb 2026 12:00:00 +0200",
        _state("malformed"),
        "RFC 5322 3.3 semantic validity: the day-of-month MUST be within the days allowed for "
        "the month IN THE SPECIFIED YEAR; 2026 is not a leap year, so this date does not exist.",
    ),
    _case(
        "time-052",
        "29 Feb 1900 12:00:00 +0000",
        _state("malformed"),
        "1900 is divisible by 100 but not by 400, so 29 Feb 1900 is not a date. A parser that "
        "answers 1900-03-01 has silently invented an instant.",
    ),
    # --- Malformed values: not an RFC 5322 date-time at all ------------------------------------
    _case(
        "time-053",
        None,
        _state("malformed"),
        "No Date header present at all: there is no source value, so neither an instant nor a "
        "zone state may be asserted; the refusal must be explicit, not a fallback to now().",
    ),
    _case(
        "time-054",
        "",
        _state("malformed"),
        "Empty header value: the 'date' production requires a day, a month and a year.",
    ),
    _case(
        "time-055",
        "   ",
        _state("malformed"),
        "Whitespace-only value: CFWS on its own is not a date-time.",
    ),
    _case(
        "time-056",
        "Whenever you get around to reading this",
        _state("malformed"),
        "English free prose in place of a date-time; nothing matches the 'date' production.",
    ),
    _case(
        "time-057",
        "утре сутринта, някъде към обяд",
        _state("malformed"),
        "Bulgarian free prose; the refusal must not depend on the text being ASCII.",
    ),
    _case(
        "time-058",
        "32 Jan 2026 09:15:00 +0200",
        _state("malformed"),
        "RFC 5322 3.3 semantic validity: the day-of-month must be 1..31 for January.",
    ),
    _case(
        "time-059",
        "12 Ян 2026 09:15:00 +0200",
        _state("malformed"),
        "Cyrillic month abbreviation: RFC 5322 3.3 'month' is one of twelve fixed ASCII tokens "
        "and RFC 6532 does not extend that production.",
    ),
    _case(
        "time-060",
        "12 Jan 2026 25:15:00 +0200",
        _state("malformed"),
        "RFC 5322 3.3: the time-of-day must lie in 00:00:00..23:59:60; hour 25 does not.",
    ),
    _case(
        "time-061",
        "12 Jan 2026 09:61:00 +0200",
        _state("malformed"),
        "Minute 61 is outside 00..59; a parser must not roll it into the next hour.",
    ),
    _case(
        "time-062",
        "12 Jan 09:15:00 +0200",
        _state("malformed"),
        "Year omitted; 'date = day month year' has no optional year.",
    ),
    _case(
        "time-063",
        "Jan 12 2026 09:15:00 +0200",
        _state("malformed"),
        "Month-before-day (asctime / US ordering) is not the RFC 5322 'date' production.",
    ),
    _case(
        "time-064",
        "2026-01-12T09:15:00+02:00",
        _state("malformed"),
        "ISO 8601 in a Date header: RFC 5322 defines no such date-time form, so no instant may "
        "be claimed from it however readable it looks to a human.",
    ),
    _case(
        "time-065",
        "12 Jan 2026 09:15:00 +02:00",
        _state("malformed"),
        "Colon inside the zone: 'zone' is FWS sign 4DIGIT and obs-zone is alphabetic, so "
        "'+02:00' matches neither production.",
    ),
    _case(
        "time-066",
        "12 Jan 2026 09:15:00 +200",
        _state("malformed"),
        "Three-digit zone: the 4DIGIT zone is fixed width; guessing '+0200' would invent an "
        "offset the sender never wrote.",
    ),
)
