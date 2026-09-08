"""SNAP span-mapping fixtures, part B (cases snap-027 ... snap-065).

Role: the second half of the public F evidence for criterion ``snap.source_span_mappings`` --
    astral-plane emoji (including a ZWJ sequence), combining marks left as stored, repeated
    fragments that make a citation genuinely ambiguous, quotes that start or end INSIDE a
    multi-character expansion, further whitespace-variant scalars, and quotes whose own
    normalization (deleted zero-width scalars, a leading BOM, typographic punctuation) is what
    makes them match. Part A (``snap_cases_a``) carries the baseline families and the record
    types; this module imports those types and adds no new ones.
Used by: ``tools.mem01_verify.fixtures.snap_cases`` (which re-exports ``SNAP_CASES``), and through
    it the SNAP gate evaluator ``tools.mem01_verify.gates.gate_snap`` and
    ``tools.mem01_verify.fixtures.digest.fixtures_digest``.
Depends on: ``tools.mem01_verify.fixtures.snap_cases_a`` for ``SnapCase``, ``SNAP_CRITERION`` and
    the three expectation builders. Nothing else inside the project; in particular NOT
    ``tools.mem01_verify.evid_norm`` -- expectations are hand-derived from contract section 6
    (contract R12, builder != labeler).
Key invariants:
    - Same invariants as part A: hand-derived expectations, MINIMAL original spans in scalars and
      UTF-8 bytes, ambiguous spans listed ascending by ``scalar_start`` but compared as a set,
      exactly one span for ``resolved`` and zero for ``unresolved``.
    - The expansion-boundary family is the sharp edge of the criterion: a quote whose only textual
      occurrence begins or ends inside the three characters that ``...`` expanded from is
      ``unresolved``, and ``snap-043`` shows the same original yielding TWO legitimate
      unit-aligned matches for ``...`` -- ambiguity, not a silent first-match pick.
    - Combining marks are ordinary scalars: NO Unicode normalization form is applied, so a
      precomposed quote must NOT match a decomposed original (``snap-031``) and its decomposed
      twin must (``snap-032``).
    - CRLF and LF are the SAME single whitespace unit, so snap-061/062 must resolve in both
      directions; snap-063/064/065 are the discriminating controls for the \\u2026 family --
      three literal periods behave nothing like one ellipsis scalar.
    - Case is preserved -- an all-caps quote never matches lowercase source, in either
      language.
    - All text is synthetic and bilingual; no real corpus text, no personal data, no addresses.
"""

from __future__ import annotations

from tools.mem01_verify.fixtures.snap_cases_a import (
    SNAP_CRITERION,
    SnapCase,
    ambiguous_spans,
    resolved_span,
    unresolved_with_reason,
)

SNAP_CASES_B: tuple[SnapCase, ...] = (
    SnapCase(
        case_id="snap-027",
        criterion_id=SNAP_CRITERION,
        origin="astral-plane emoji \\U0001f680 is ONE scalar and FOUR UTF-8 bytes",
        original="Стартираме 🚀 днес",
        quote="🚀",
        expected=resolved_span(11, 12, 21, 25),
    ),
    SnapCase(
        case_id="snap-028",
        criterion_id=SNAP_CRITERION,
        origin="an emoji is neither whitespace nor deleted: a quote may span straight through it",
        original="Deploy 🚀 done",
        quote="Deploy 🚀 done",
        expected=resolved_span(0, 13, 0, 16),
    ),
    SnapCase(
        case_id="snap-029",
        criterion_id=SNAP_CRITERION,
        origin="ZWJ \\u200d inside an emoji sequence is deleted, so the ZWJ-less quote matches",
        original="Ролята 👨\u200d💻 е заета",
        quote="👨💻",
        expected=resolved_span(7, 10, 13, 24),
    ),
    SnapCase(
        case_id="snap-030",
        criterion_id=SNAP_CRITERION,
        origin="twin of snap-029: the ZWJ-bearing quote maps to the SAME original span",
        original="Ролята 👨\u200d💻 е заета",
        quote="👨\u200d💻",
        expected=resolved_span(7, 10, 13, 24),
    ),
    SnapCase(
        case_id="snap-031",
        criterion_id=SNAP_CRITERION,
        origin="NO Unicode normalization form is applied: precomposed \\u00e9 misses e+\\u0301",
        original="Cafe\u0301 report",
        quote="Caf\u00e9",
        expected=unresolved_with_reason("no_unit_aligned_occurrence"),
    ),
    SnapCase(
        case_id="snap-032",
        criterion_id=SNAP_CRITERION,
        origin="positive twin of snap-031: the decomposed quote matches scalar for scalar",
        original="Cafe\u0301 report",
        quote="Cafe\u0301",
        expected=resolved_span(0, 5, 0, 6),
    ),
    SnapCase(
        case_id="snap-033",
        criterion_id=SNAP_CRITERION,
        origin="a Cyrillic base + combining acute \\u0301 stays a two-scalar, four-byte sequence",
        original="Планира\u0301не готово",
        quote="Планира\u0301не",
        expected=resolved_span(0, 10, 0, 20),
    ),
    SnapCase(
        case_id="snap-034",
        criterion_id=SNAP_CRITERION,
        origin="a combining mark starts its own unit, so a quote may legally end just before it",
        original="Планира\u0301не готово",
        quote="Планира",
        expected=resolved_span(0, 7, 0, 14),
    ),
    SnapCase(
        case_id="snap-035",
        criterion_id=SNAP_CRITERION,
        origin="a repeated fragment is ambiguous: every distinct original interval is returned",
        original="Ок. Ок. Готово",
        quote="Ок.",
        expected=ambiguous_spans((0, 3, 0, 5), (4, 7, 6, 11)),
    ),
    SnapCase(
        case_id="snap-036",
        criterion_id=SNAP_CRITERION,
        origin="ambiguity CREATED by normalization: straight and typographic quotes coincide",
        original='Той каза "край" и после \u201eкрай\u201c пак.',
        quote='"край"',
        expected=ambiguous_spans((9, 15, 16, 26), (24, 30, 41, 55)),
    ),
    SnapCase(
        case_id="snap-037",
        criterion_id=SNAP_CRITERION,
        origin="three occurrences: ambiguity is not capped at two candidate spans",
        original="AB AB AB",
        quote="AB",
        expected=ambiguous_spans((0, 2, 0, 2), (3, 5, 3, 5), (6, 8, 6, 8)),
    ),
    SnapCase(
        case_id="snap-038",
        criterion_id=SNAP_CRITERION,
        origin="ambiguity where one candidate spans a collapsed run and the other a single space",
        original="cena  100 и cena 100",
        quote="cena 100",
        expected=ambiguous_spans((0, 9, 0, 9), (12, 20, 13, 21)),
    ),
    SnapCase(
        case_id="snap-039",
        criterion_id=SNAP_CRITERION,
        origin="a quote starting inside the \\u2026 expansion has no unit-aligned occurrence",
        original="Ще проверя\u2026утре",
        quote="..утре",
        expected=unresolved_with_reason("occurrence_starts_inside_expansion"),
    ),
    SnapCase(
        case_id="snap-040",
        criterion_id=SNAP_CRITERION,
        origin="a quote ending inside the \\u2026 expansion has no unit-aligned occurrence",
        original="Ще проверя\u2026утре",
        quote="проверя..",
        expected=unresolved_with_reason("occurrence_ends_inside_expansion"),
    ),
    SnapCase(
        case_id="snap-041",
        criterion_id=SNAP_CRITERION,
        origin="\\u2026 followed by a literal '.': '....' spans two units, original span 2 scalars",
        original="Край\u2026. Ново",
        quote="....",
        expected=resolved_span(4, 6, 8, 12),
    ),
    SnapCase(
        case_id="snap-042",
        criterion_id=SNAP_CRITERION,
        origin="two adjacent \\u2026: six dots is the only unit-aligned occurrence",
        original="Хм\u2026\u2026край",
        quote="......",
        expected=resolved_span(2, 4, 4, 10),
    ),
    SnapCase(
        case_id="snap-043",
        criterion_id=SNAP_CRITERION,
        origin="same original: '...' aligns with EITHER \\u2026 unit -- ambiguous, not first-win",
        original="Хм\u2026\u2026край",
        quote="...",
        expected=ambiguous_spans((2, 3, 4, 7), (3, 4, 7, 10)),
    ),
    SnapCase(
        case_id="snap-044",
        criterion_id=SNAP_CRITERION,
        origin="space + NBSP + space is ONE unit; the span covers all three original scalars",
        original="Отчет \u00a0 готов",
        quote="Отчет готов",
        expected=resolved_span(0, 13, 0, 24),
    ),
    SnapCase(
        case_id="snap-045",
        criterion_id=SNAP_CRITERION,
        origin="a trailing space in the quote is trimmed: the span excludes the following run",
        original="Данни: 42 лв.",
        quote="Данни: 42 ",
        expected=resolved_span(0, 9, 0, 14),
    ),
    SnapCase(
        case_id="snap-046",
        criterion_id=SNAP_CRITERION,
        origin="quoting the whole artifact returns [first_kept, last_kept + 1), not [0, len)",
        original="  Итог: 7  ",
        quote="Итог: 7",
        expected=resolved_span(2, 9, 2, 13),
    ),
    SnapCase(
        case_id="snap-047",
        criterion_id=SNAP_CRITERION,
        origin="mixed BG/EN line: em dash and a digit-group NBSP normalize inside one quote",
        original="Итог \u2014 1\u00a0000 EUR",
        quote="Итог - 1 000 EUR",
        expected=resolved_span(0, 16, 0, 23),
    ),
    SnapCase(
        case_id="snap-048",
        criterion_id=SNAP_CRITERION,
        origin="leading and trailing CRLF are trimmed; an interior CRLFCRLF is one interior unit",
        original="\r\nРед 1\r\n\r\nРед 2\r\n",
        quote="Ред 1 Ред 2",
        expected=resolved_span(2, 16, 2, 22),
    ),
    SnapCase(
        case_id="snap-049",
        criterion_id=SNAP_CRITERION,
        origin="a tab run is whitespace: 'A B' spans the whole three-tab run between the letters",
        original="A\t\t\tB",
        quote="A B",
        expected=resolved_span(0, 5, 0, 5),
    ),
    SnapCase(
        case_id="snap-050",
        criterion_id=SNAP_CRITERION,
        origin="a quote absent from the artifact is unresolved, never a nearest-neighbour guess",
        original="Само този текст.",
        quote="друг текст",
        expected=unresolved_with_reason("no_unit_aligned_occurrence"),
    ),
    SnapCase(
        case_id="snap-051",
        criterion_id=SNAP_CRITERION,
        origin="a quote longer than the artifact is unresolved, not a partial match",
        original="Кратко",
        quote="Кратко и дълго",
        expected=unresolved_with_reason("no_unit_aligned_occurrence"),
    ),
    SnapCase(
        case_id="snap-052",
        criterion_id=SNAP_CRITERION,
        origin="case is preserved: an upper-case Cyrillic quote misses lower-case source",
        original="отчет готов",
        quote="ОТЧЕТ",
        expected=unresolved_with_reason("no_unit_aligned_occurrence"),
    ),
    SnapCase(
        case_id="snap-053",
        criterion_id=SNAP_CRITERION,
        origin="English twin of snap-052: normalization never folds case in either script",
        original="Report Ready",
        quote="report",
        expected=unresolved_with_reason("no_unit_aligned_occurrence"),
    ),
    SnapCase(
        case_id="snap-054",
        criterion_id=SNAP_CRITERION,
        origin="figure space \\u2007 and punctuation space \\u2008 each become one space unit",
        original="12\u2007345\u2008EUR",
        quote="12 345 EUR",
        expected=resolved_span(0, 10, 0, 14),
    ),
    SnapCase(
        case_id="snap-055",
        criterion_id=SNAP_CRITERION,
        origin="ideographic space \\u3000 becomes a space unit; it is 3 bytes in the original",
        original="Проект\u3000Алфа",
        quote="Проект Алфа",
        expected=resolved_span(0, 11, 0, 23),
    ),
    SnapCase(
        case_id="snap-056",
        criterion_id=SNAP_CRITERION,
        origin='prime \\u2032 is in the frozen apostrophe table and normalizes to "\'"',
        original="5\u2032 ниво",
        quote="5' ниво",
        expected=resolved_span(0, 7, 0, 13),
    ),
    SnapCase(
        case_id="snap-057",
        criterion_id=SNAP_CRITERION,
        origin='modifier letter apostrophe \\u02bc normalizes to "\'" and is 2 bytes, not 3',
        original="OK\u02bcs data",
        quote="OK's",
        expected=resolved_span(0, 4, 0, 5),
    ),
    SnapCase(
        case_id="snap-058",
        criterion_id=SNAP_CRITERION,
        origin='single low-9 \\u201a and single right angle \\u203a both normalize to "\'"',
        original="Реф \u201aтест\u203a край",
        quote="'тест'",
        expected=resolved_span(4, 10, 7, 21),
    ),
    SnapCase(
        case_id="snap-059",
        criterion_id=SNAP_CRITERION,
        origin="deletion applies to the QUOTE too: an interior \\u200b in the quote is removed",
        original="Сума 100",
        quote="Су\u200bма 100",
        expected=resolved_span(0, 8, 0, 12),
    ),
    SnapCase(
        case_id="snap-060",
        criterion_id=SNAP_CRITERION,
        origin="a BOM \\ufeff pasted at the head of a quote is deleted, not matched literally",
        original="Плащане прието",
        quote="\ufeffПлащане",
        expected=resolved_span(0, 7, 0, 14),
    ),
    SnapCase(
        case_id="snap-061",
        criterion_id=SNAP_CRITERION,
        origin="CRLF vs LF: a quote typed with a bare LF matches a CRLF original (both one unit)",
        original="Ред 1\r\nРед 2",
        quote="Ред 1\nРед 2",
        expected=resolved_span(0, 12, 0, 18),
    ),
    SnapCase(
        case_id="snap-062",
        criterion_id=SNAP_CRITERION,
        origin="mirror of snap-061: a CRLF-bearing quote matches an LF original, span 11 scalars",
        original="Ред 1\nРед 2",
        quote="Ред 1\r\nРед 2",
        expected=resolved_span(0, 11, 0, 17),
    ),
    SnapCase(
        case_id="snap-063",
        criterion_id=SNAP_CRITERION,
        origin="snap-041 original: only the LITERAL period is unit-aligned, never \\u2026",
        original="Край\u2026. Ново",
        quote=".",
        expected=resolved_span(5, 6, 11, 12),
    ),
    SnapCase(
        case_id="snap-064",
        criterion_id=SNAP_CRITERION,
        origin="control for snap-009: three LITERAL periods give three unit-aligned '.' matches",
        original="Wait... done",
        quote=".",
        expected=ambiguous_spans((4, 5, 4, 5), (5, 6, 5, 6), (6, 7, 6, 7)),
    ),
    SnapCase(
        case_id="snap-065",
        criterion_id=SNAP_CRITERION,
        origin="control for snap-010: a \\u2026 QUOTE matches three literal periods",
        original="Wait... done",
        quote="\u2026",
        expected=resolved_span(4, 7, 4, 7),
    ),
)
