"""
Role: The HTML, RTF and plain-text halves of the FID fixture battery — `fid-053..065` (html),
      `fid-066..078` (rtf) and `fid-079..090` (text). Each case renders one synthetic ORIGINAL
      from `fid_cases_b` and pairs it with the expectation derived from that same ORIGINAL
      (contract R12, section 10.5).
Used by: `fid_cases.py` (concatenates every half into `build_fid_cases()`).
Depends on: `fid_cases_a` (record vocabulary, `expectation_for_doc`, `fid_case`), `fid_cases_b`
            (the ORIGINAL library, `boundary_sequence` and the two-column blocks),
            `fid_builders_c` (the html / rtf / plain-text renderers). Nothing from `backend/app/`
            or `backend/tests/`.
Key invariants:
  - HTML has no pages, so the "page/column order" unit of the taxonomy is COLUMN ORDER: the three
    full-taxonomy html cases each carry a two-column layout whose reading order is column one in
    full, then column two. The layout blocks sit between the list items and the first table in the
    ORIGINAL, and the expectation says so.
  - RTF has real pages: the three full-taxonomy rtf cases emit a `\\page` break before the first
    table, and the boundary is stated through `boundary_sequence`.
  - PLAIN TEXT cannot carry a table, a link relationship or a column layout, so no plain-text case
    claims `taxonomy_complete`. Its ORIGINALS are the flat-line renderings: what must survive is
    the line order, the cell separators inside a rendered row, the negations and the grouped
    amounts. A link rides as `label <destination>` on one line, which is what a plain-text
    ORIGINAL can express.
  - Every html case declares its charset truthfully and is encoded in the charset it declares; the
    deliberately LYING declarations live in the encoding battery, never here.
  - Bulgarian and English ORIGINALS are both represented in each of the three formats.
"""

from __future__ import annotations

from tools.mem01_verify.fixtures.fid_builders_c import html_from_doc, rtf_from_doc, text_from_doc
from tools.mem01_verify.fixtures.fid_cases_a import (
    DocSpec,
    FidCase,
    FidExpectation,
    FidUnit,
    expectation_for_doc,
    fid_case,
)
from tools.mem01_verify.fixtures.fid_cases_b import (
    COLLAPSED_AMOUNTS,
    CYRILLIC_ORIGINALS,
    EN_INVOICE,
    LATIN_ORIGINALS,
    TWO_COLUMN_LEFT,
    TWO_COLUMN_RIGHT,
    boundary_sequence,
)

HTML_CONTENT_TYPE = "text/html"
RTF_CONTENT_TYPE = "text/rtf"
RTF_APPLICATION_CONTENT_TYPE = "application/rtf"
TEXT_CONTENT_TYPE = "text/plain; charset=utf-8"


def _plain(spec: DocSpec) -> FidExpectation:
    """The expectation of an ORIGINAL rendered as one continuous flow."""
    return expectation_for_doc(spec, forbidden=COLLAPSED_AMOUNTS)


def _two_column_units() -> tuple[FidUnit, ...]:
    """The layout blocks of the two-column ORIGINAL, in the ORIGINAL's reading order."""
    left = tuple(
        FidUnit(f"c1b{index}", "layout_block", text, layout_column=1)
        for index, text in enumerate(TWO_COLUMN_LEFT, start=1)
    )
    right = tuple(
        FidUnit(f"c2b{index}", "layout_block", text, layout_column=2)
        for index, text in enumerate(TWO_COLUMN_RIGHT, start=1)
    )
    return left + right


def _html_taxonomy_expectation(spec: DocSpec) -> FidExpectation:
    """Full-taxonomy html expectation: the document flow with the two-column layout spliced in.

    The layout blocks are emitted between the list items and the first table, so the ORIGINAL's
    reading order is: paragraphs, list items, column one top to bottom, column two top to bottom,
    then the tables, links and trailing paragraphs.
    """
    base = expectation_for_doc(spec, taxonomy_complete=True, forbidden=COLLAPSED_AMOUNTS)
    layout = _two_column_units()
    before, after = boundary_sequence(spec)
    stitched = (before, *(unit.unit_id for unit in layout), after)
    return FidExpectation(
        units=base.units + layout,
        ordered_sequences=base.ordered_sequences + (stitched,),
        row_groups=base.row_groups,
        link_pairs=base.link_pairs,
        negation_guards=base.negation_guards,
        forbidden=base.forbidden,
        taxonomy_complete=True,
    )


def build_html_cases() -> tuple[FidCase, ...]:
    """Build the html half of the battery (`fid-053` .. `fid-065`).

    Three full-taxonomy documents carrying a two-column layout (two Bulgarian, one English), then
    ten single-flow documents over the remaining ORIGINALS.

    Returns:
        The html cases in `case_id` order.
    """
    taxonomy = (
        ("фактура на кирилица с двуколонен блок преди таблицата", CYRILLIC_ORIGINALS[0][1]),
        ("протокол на кирилица с двуколонен блок и отрицание", CYRILLIC_ORIGINALS[1][1]),
        ("English invoice with a two-column block before the settlement table", EN_INVOICE),
    )
    cases: list[FidCase] = []
    for index, (origin, spec) in enumerate(taxonomy, start=53):
        cases.append(
            fid_case(
                f"fid-{index:03d}",
                "full unit taxonomy in html — "
                f"{origin}: ordered paragraphs and list items, real <a href> pairs, a two-column "
                "reading order and <table>/<th> tuples with header association",
                "html",
                HTML_CONTENT_TYPE,
                html_from_doc(spec, two_column_blocks=(TWO_COLUMN_LEFT, TWO_COLUMN_RIGHT)),
                _html_taxonomy_expectation(spec),
            )
        )
    remaining = [*CYRILLIC_ORIGINALS[2:], *LATIN_ORIGINALS[1:]]
    for index, (origin, spec) in enumerate(remaining[:10], start=56):
        cases.append(
            fid_case(
                f"fid-{index:03d}",
                f"html document — {origin}",
                "html",
                HTML_CONTENT_TYPE,
                html_from_doc(spec),
                _plain(spec),
            )
        )
    return tuple(cases)


def build_rtf_cases() -> tuple[FidCase, ...]:
    """Build the rtf half of the battery (`fid-066` .. `fid-078`).

    Three full-taxonomy documents with a real `\\page` break before the table (two Bulgarian, one
    English), then ten single-flow documents; one of them declares `application/rtf` so both
    dispatch types the ingest path accepts are exercised.

    Returns:
        The rtf cases in `case_id` order.
    """
    taxonomy = (
        ("фактура на кирилица с page break преди таблицата", CYRILLIC_ORIGINALS[0][1]),
        ("протокол на кирилица с page break и отрицание в таблицата", CYRILLIC_ORIGINALS[1][1]),
        ("English invoice with a page break before the settlement table", EN_INVOICE),
    )
    cases: list[FidCase] = []
    for index, (origin, spec) in enumerate(taxonomy, start=66):
        cases.append(
            fid_case(
                f"fid-{index:03d}",
                "full unit taxonomy in rtf — "
                f"{origin}: ordered paragraphs and list items, a HYPERLINK field whose destination "
                "is not typed in its label, a page boundary and \\trowd table tuples",
                "rtf",
                RTF_CONTENT_TYPE,
                rtf_from_doc(spec, page_break_before_tables=True),
                expectation_for_doc(
                    spec,
                    taxonomy_complete=True,
                    forbidden=COLLAPSED_AMOUNTS,
                    extra_sequences=(boundary_sequence(spec),),
                ),
            )
        )
    remaining = [*CYRILLIC_ORIGINALS[2:], *LATIN_ORIGINALS[1:]]
    for index, (origin, spec) in enumerate(remaining[:10], start=69):
        content_type = RTF_APPLICATION_CONTENT_TYPE if index == 69 else RTF_CONTENT_TYPE
        cases.append(
            fid_case(
                f"fid-{index:03d}",
                f"rtf document ({content_type}) — {origin}",
                "rtf",
                content_type,
                rtf_from_doc(spec),
                _plain(spec),
            )
        )
    return tuple(cases)


def build_text_cases() -> tuple[FidCase, ...]:
    """Build the plain-text half of the battery (`fid-079` .. `fid-090`).

    Twelve flat-line ORIGINALS (six Bulgarian, six English). None claims `taxonomy_complete`:
    plain text carries no table, no link relationship and no column layout, so the units that must
    survive are the lines, their order, the cell separators inside a rendered row, the negations
    and the grouped amounts. One case uses CRLF line endings — a frozen whitespace alternative.

    Returns:
        The text cases in `case_id` order.
    """
    specs = [*CYRILLIC_ORIGINALS, *LATIN_ORIGINALS[:6]]
    cases: list[FidCase] = []
    for index, (origin, spec) in enumerate(specs, start=79):
        newline = "\r\n" if index == 84 else "\n"
        ending = "CRLF" if newline == "\r\n" else "LF"
        cases.append(
            fid_case(
                f"fid-{index:03d}",
                f"plain text ({ending} line endings) — {origin}",
                "text",
                TEXT_CONTENT_TYPE,
                text_from_doc(spec, newline=newline),
                _plain(spec),
            )
        )
    return tuple(cases)
