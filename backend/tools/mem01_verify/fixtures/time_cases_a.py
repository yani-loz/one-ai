"""TIME battery, part A — numeric offsets, absent zones, obsolete zones, DST, obsolete years.

Role:
    The first contiguous half of the TIME conformance battery, together with the record type and
    the three private builders both halves share. It carries ``TIME_CASES_A``: explicit numeric
    offsets, the ``-0000`` / missing-zone cases, the obsolete alphabetic zones of RFC 2822 4.3,
    the Europe/Sofia DST transitions, and the obsolete year syntax.

Used by:
    ``tools.mem01_verify.fixtures.time_cases`` (the public aggregator named in contract section
    1.3, which concatenates part A and part B into ``TIME_CASES``), and
    ``tools.mem01_verify.fixtures.time_cases_b`` (for ``TimeCase`` and the builders).

Depends on:
    Nothing inside the project. Standard library only, by design: a fixture module that
    imported a measured component could not honour contract R12.

Key invariants:
    - The A/B split is a file-size measure only (`.claude/rules/code-quality.md` A2) and carries
      no semantics. Part A is a contiguous PREFIX of the battery: ``TIME_CASES_A + TIME_CASES_B``
      reproduces the original record order exactly, so the battery digest is unchanged by it.
    - The battery's full invariant set — the three ``expected`` shapes, the ``-0000`` rule, the
      whole-second ``instant_utc`` form, day-of-week consistency, id uniqueness and the PII-free
      rule — is stated once, in ``time_cases``, and governs the records here.
    - **R12.** Every ``expected`` value was derived by hand from RFC 5322 sections 3.3 / 4.3 (and
      RFC 2822 section 4.3 for obsolete years), never by running ``parse_date``, ``headers.py``
      or any other measured component.
    - No gate imports ``TIME_CASES_A``; gates import ``TIME_CASES`` from ``time_cases``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

TIME_CRITERION_ID = "time.fixtures"


@dataclass(frozen=True, slots=True)
class TimeCase:
    """One ``Date``-header conformance case with an independently specified expectation.

    Attributes:
        case_id: Unique identifier of the form ``time_cases-NNN``.
        criterion_id: The criteria-file criterion this case scores (always ``time.fixtures``).
        origin: The rule the case pins — the RFC clause the expectation was derived from.
        header_value: The raw ``Date`` header value BEFORE header unfolding; a literal CRLF in
            the value is a real fold the consumer must unfold. ``None`` models a message that
            carries no ``Date`` header at all.
        expected: One of the three shapes documented in the module invariants.
    """

    case_id: str
    criterion_id: str
    origin: str
    header_value: str | None
    expected: Mapping[str, object]


def _instant(instant_utc: str) -> Mapping[str, object]:
    """Build the expectation for a header whose zone is known, so the instant is determined."""
    return {"instant_utc": instant_utc, "offset_known": True}


def _state(state: str) -> Mapping[str, object]:
    """Build the expectation for a header that yields no instant: unknown_zone or malformed."""
    return {"state": state}


def _case(
    case_id: str, header_value: str | None, expected: Mapping[str, object], origin: str
) -> TimeCase:
    """Build a TIME case, stamping the single criterion this whole battery scores."""
    return TimeCase(
        case_id=case_id,
        criterion_id=TIME_CRITERION_ID,
        origin=origin,
        header_value=header_value,
        expected=expected,
    )


# Calendar facts used below, established by hand from the proleptic Gregorian calendar:
# 2026-01-01 Thu · 2026-03-29 Sun (Europe/Sofia spring transition) · 2026-10-25 Sun (autumn
# transition) · 2024-02-29 Thu · 2000-02-29 Tue · 1999-12-31 Fri · 2026-12-31 Thu ·
# 2027-01-01 Fri. Europe/Sofia switches at 01:00 UTC on both transition days.
TIME_CASES_A: tuple[TimeCase, ...] = (
    # --- Explicit numeric offsets (RFC 5322 3.3: zone = ("+" / "-") 4DIGIT) ------------------
    _case(
        "time-001",
        "Mon, 12 Jan 2026 09:15:00 +0200",
        _instant("2026-01-12T07:15:00Z"),
        "RFC 5322 3.3 numeric zone; Europe/Sofia standard time (EET) is +0200 in January.",
    ),
    _case(
        "time-002",
        "Wed, 15 Jul 2026 09:15:00 +0300",
        _instant("2026-07-15T06:15:00Z"),
        "RFC 5322 3.3 numeric zone; Europe/Sofia summer time (EEST) is +0300 in July.",
    ),
    _case(
        "time-003",
        "Tue, 03 Feb 2026 23:45:00 +0300",
        _instant("2026-02-03T20:45:00Z"),
        "Positive offset late in the local day that does NOT roll the UTC date backwards.",
    ),
    _case(
        "time-004",
        "Thu, 05 Mar 2026 08:00:00 -0500",
        _instant("2026-03-05T13:00:00Z"),
        "Negative offset: subtracting a negative zone moves the instant forward.",
    ),
    _case(
        "time-005",
        "Sat, 21 Nov 2026 16:20:00 +0000",
        _instant("2026-11-21T16:20:00Z"),
        "RFC 5322 3.3: '+0000' is the form that DOES assert Universal Time.",
    ),
    _case(
        "time-006",
        "Fri, 10 Apr 2026 10:45:00 +0530",
        _instant("2026-04-10T05:15:00Z"),
        "Half-hour offset; the minutes field of the zone must be honoured, not truncated.",
    ),
    _case(
        "time-007",
        "Mon, 12 Jan 2026 09:15:00 -0230",
        _instant("2026-01-12T11:45:00Z"),
        "Negative half-hour offset; the sign applies to hours AND minutes together.",
    ),
    _case(
        "time-008",
        "Fri, 01 Jan 2027 11:00:00 +1400",
        _instant("2026-12-31T21:00:00Z"),
        "Extreme positive offset rolls the UTC date BACK across a year boundary.",
    ),
    _case(
        "time-009",
        "Thu, 31 Dec 2026 20:30:00 -1200",
        _instant("2027-01-01T08:30:00Z"),
        "Extreme negative offset rolls the UTC date FORWARD across a year boundary.",
    ),
    # --- '-0000' and a missing zone: no zone knowledge, therefore no instant ------------------
    _case(
        "time-010",
        "Mon, 12 Jan 2026 09:15:00 -0000",
        _state("unknown_zone"),
        "RFC 5322 3.3: '-0000' means the date-time carries NO information about the local "
        "zone. It must not be laundered into +0000; the state is frozen as unknown.",
    ),
    _case(
        "time-011",
        "Wed, 15 Jul 2026 23:59:59 -0000",
        _state("unknown_zone"),
        "Second '-0000' case, at a day boundary: an assumed UTC would also fake the UTC DATE.",
    ),
    _case(
        "time-012",
        "Mon, 12 Jan 2026 09:15:00",
        _state("unknown_zone"),
        "No zone token at all — the naive header the criterion targets. A missing zone gets a "
        "frozen unknown state, never a known offset.",
    ),
    _case(
        "time-013",
        "12 Jan 2026 09:15:00",
        _state("unknown_zone"),
        "No zone and no day-of-week (day-of-week is optional in RFC 5322 3.3); still unknown.",
    ),
    # --- Obsolete alphabetic zones (RFC 5322 4.3, obs-zone) -----------------------------------
    _case(
        "time-014",
        "Mon, 12 Jan 2026 09:15:00 GMT",
        _instant("2026-01-12T09:15:00Z"),
        "RFC 5322 4.3: 'GMT' is equivalent to +0000 — a KNOWN zone, unlike '-0000'.",
    ),
    _case(
        "time-015",
        "Mon, 12 Jan 2026 09:15:00 UT",
        _instant("2026-01-12T09:15:00Z"),
        "RFC 5322 4.3: 'UT' is equivalent to +0000.",
    ),
    _case(
        "time-016",
        "Thu, 05 Mar 2026 08:00:00 EST",
        _instant("2026-03-05T13:00:00Z"),
        "RFC 5322 4.3: 'EST' is -0500.",
    ),
    _case(
        "time-017",
        "Fri, 10 Apr 2026 08:00:00 EDT",
        _instant("2026-04-10T12:00:00Z"),
        "RFC 5322 4.3: 'EDT' is -0400.",
    ),
    _case(
        "time-018",
        "Mon, 12 Jan 2026 09:15:00 CST",
        _instant("2026-01-12T15:15:00Z"),
        "RFC 5322 4.3: 'CST' is -0600.",
    ),
    _case(
        "time-019",
        "Wed, 15 Jul 2026 09:15:00 CDT",
        _instant("2026-07-15T14:15:00Z"),
        "RFC 5322 4.3: 'CDT' is -0500 — the same offset as EST, so the NAME must be read.",
    ),
    _case(
        "time-020",
        "Sat, 21 Nov 2026 06:00:00 MST",
        _instant("2026-11-21T13:00:00Z"),
        "RFC 5322 4.3: 'MST' is -0700.",
    ),
    _case(
        "time-021",
        "Wed, 15 Jul 2026 09:15:00 MDT",
        _instant("2026-07-15T15:15:00Z"),
        "RFC 5322 4.3: 'MDT' is -0600.",
    ),
    _case(
        "time-022",
        "Mon, 12 Jan 2026 09:15:00 PST",
        _instant("2026-01-12T17:15:00Z"),
        "RFC 5322 4.3: 'PST' is -0800.",
    ),
    _case(
        "time-023",
        "Wed, 15 Jul 2026 06:00:00 PDT",
        _instant("2026-07-15T13:00:00Z"),
        "RFC 5322 4.3: 'PDT' is -0700.",
    ),
    _case(
        "time-024",
        "Mon, 12 Jan 2026 09:15:00 A",
        _state("unknown_zone"),
        "RFC 5322 4.3: single-character military zones SHOULD be considered equivalent to "
        "'-0000' unless out-of-band information confirms them — i.e. no zone knowledge.",
    ),
    _case(
        "time-025",
        "Mon, 12 Jan 2026 09:15:00 Z",
        _state("unknown_zone"),
        "'Z' is a single-character military zone under RFC 5322 4.3, so it too degrades to "
        "'-0000'. Reading it as Zulu/UTC is exactly the fake-UTC failure the criterion bans.",
    ),
    _case(
        "time-026",
        "Mon, 12 Jan 2026 09:15:00 CET",
        _state("unknown_zone"),
        "RFC 5322 4.3: a multi-character alphabetic zone outside the obs-zone list whose "
        "meaning is not known SHOULD be considered equivalent to '-0000'.",
    ),
    _case(
        "time-027",
        "Wed, 15 Jul 2026 09:15:00 EEST",
        _state("unknown_zone"),
        "The same rule for the Sofia summer abbreviation: obvious to a human, unknown to the "
        "RFC, so the offset must not be guessed as +0300.",
    ),
    _case(
        "time-028",
        "Mon, 12 Jan 2026 09:15:00 J",
        _state("malformed"),
        "RFC 5322 4.3 obs-zone excludes 'J' from the military ranges (%d65-73 / %d75-90), so "
        "the token matches no zone production at all and the date-time does not parse.",
    ),
    # --- DST transitions for Europe/Sofia, written with explicit offsets ----------------------
    _case(
        "time-029",
        "Sun, 29 Mar 2026 02:59:59 +0200",
        _instant("2026-03-29T00:59:59Z"),
        "Last EET second before Europe/Sofia springs forward (transition at 01:00 UTC).",
    ),
    _case(
        "time-030",
        "Sun, 29 Mar 2026 04:00:00 +0300",
        _instant("2026-03-29T01:00:00Z"),
        "First EEST second after the spring transition; 029 and 030 are one second apart.",
    ),
    _case(
        "time-031",
        "Sun, 29 Mar 2026 03:30:00 +0200",
        _instant("2026-03-29T01:30:00Z"),
        "A local wall time that does not exist in Europe/Sofia that day. RFC 5322 fixes the "
        "instant from the explicit offset alone; zone-database validity is irrelevant.",
    ),
    _case(
        "time-032",
        "Sun, 25 Oct 2026 03:30:00 +0300",
        _instant("2026-10-25T00:30:00Z"),
        "Autumn fall-back: first pass of the repeated 03:30 wall time, still EEST.",
    ),
    _case(
        "time-033",
        "Sun, 25 Oct 2026 03:30:00 +0200",
        _instant("2026-10-25T01:30:00Z"),
        "Second pass of the same wall time, now EET. 032 and 033 prove the offset — not the "
        "clock face — decides: they differ by exactly one hour.",
    ),
    _case(
        "time-034",
        "Sun, 25 Oct 2026 03:59:59 +0300",
        _instant("2026-10-25T00:59:59Z"),
        "Last EEST second before the autumn transition at 01:00 UTC.",
    ),
    _case(
        "time-035",
        "Sun, 25 Oct 2026 03:00:00 +0200",
        _instant("2026-10-25T01:00:00Z"),
        "First EET second after the autumn transition.",
    ),
    # --- Obsolete year syntax (RFC 2822 4.3 obs-year, retained by RFC 5322 4.3) ---------------
    _case(
        "time-036",
        "Mon, 12 Jan 26 09:15:00 +0200",
        _instant("2026-01-12T07:15:00Z"),
        "RFC 2822 4.3: a two-digit year between 00 and 49 is interpreted by adding 2000.",
    ),
    _case(
        "time-037",
        "Fri, 31 Dec 99 23:00:00 +0200",
        _instant("1999-12-31T21:00:00Z"),
        "RFC 2822 4.3: a two-digit year between 50 and 99 is interpreted by adding 1900.",
    ),
    _case(
        "time-038",
        "01 Jan 49 00:30:00 +0200",
        _instant("2048-12-31T22:30:00Z"),
        "Upper edge of the 'add 2000' band (49 -> 2049) AND a backwards UTC-date roll.",
    ),
    _case(
        "time-039",
        "01 Jan 50 12:00:00 +0000",
        _instant("1950-01-01T12:00:00Z"),
        "Lower edge of the 'add 1900' band (50 -> 1950); one digit away from case 038.",
    ),
    _case(
        "time-040",
        "12 Jan 102 09:15:00 +0200",
        _instant("2002-01-12T07:15:00Z"),
        "RFC 2822 4.3: ANY three-digit year is interpreted by adding 1900 (102 -> 2002).",
    ),
)
