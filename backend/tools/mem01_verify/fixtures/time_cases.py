"""TIME battery — public, synthetic ``Date``-header conformance fixtures (FIXTURES_V1).

Role:
    Public F evidence for the TIME gate (contract section 10.2, criterion ``time.fixtures``).
    Each record pairs one RFC 5322 / RFC 2822 ``Date`` header value with the instant (or the
    refusal state) that the RFC itself prescribes, so a date parser can be scored without the
    fixture having ever asked a parser what the answer is. This module is the single import
    point: it concatenates the two authored halves into ``TIME_CASES`` and re-exports the record
    type and the criterion id.

Used by:
    ``tools.mem01_verify.gates.gate_time`` (scores the TIME gate);
    ``tools.mem01_verify.fixtures.digest`` (the battery digest that enters ``config_hash``).

Depends on:
    ``tools.mem01_verify.fixtures.time_cases_a`` (record type, builders, and the first contiguous
    half of the records) and ``…time_cases_b`` (the second half) — data only. Nothing else
    inside the project: a fixture module that imported a measured component could not honour
    contract R12.

Key invariants:
    - **R12.** Every ``expected`` value was derived by hand from RFC 5322 sections 3.3 / 4.3
      (and RFC 2822 section 4.3 for obsolete years), never by running ``parse_date``,
      ``headers.py`` or any other measured component. Where the current ingest code disagrees
      (naive dates and ``-0000`` are stamped UTC today, contract section 11) the RFC answer
      stands and the fixture is expected to FAIL until the code is fixed.
    - ``expected`` has exactly one of three shapes and no others:
      ``{"instant_utc": "YYYY-MM-DDTHH:MM:SSZ", "offset_known": True}`` (a known zone fixes the
      instant), ``{"state": "unknown_zone"}`` (the header carries no usable zone information,
      so no instant may be claimed), ``{"state": "malformed"}`` (the value is not an RFC 5322
      ``date-time`` at all).
    - ``-0000``, a missing zone, an unrecognised alphabetic zone and a single-character
      military zone are ``unknown_zone``. They are NEVER ``+0000``: RFC 5322 section 3.3 states
      that ``-0000`` indicates the time was generated on a system that may be in a local time
      zone other than Universal Time and that the date-time contains no information about the
      local time zone.
    - ``instant_utc`` is always a whole second in ``YYYY-MM-DDTHH:MM:SSZ`` form. Leap seconds
      (``23:59:60``, permitted by RFC 5322) are absent — the shape cannot express them.
    - Every included ``day-of-week`` matches the day implied by its date (RFC 5322 section 3.3
      makes that a MUST), so no case's outcome hinges on weekday consistency.
    - Every ``case_id`` is unique; every ``criterion_id`` is ``time.fixtures``.
    - PII-free and corpus-free: no real corpus text, no personal names, no email addresses.
    - The halves exist only to stay under the file-size ceiling of
      `.claude/rules/code-quality.md` A2; the split carries no semantics. ``TIME_CASES`` is
      ``TIME_CASES_A + TIME_CASES_B`` in that order, which is the order the records were
      authored in, so moving a record between halves would change the battery digest.
"""

from __future__ import annotations

from tools.mem01_verify.fixtures.time_cases_a import (
    TIME_CASES_A,
    TIME_CRITERION_ID,
    TimeCase,
)
from tools.mem01_verify.fixtures.time_cases_b import TIME_CASES_B

TIME_CASES: tuple[TimeCase, ...] = TIME_CASES_A + TIME_CASES_B

__all__ = [
    "TIME_CASES",
    "TIME_CRITERION_ID",
    "TimeCase",
]
