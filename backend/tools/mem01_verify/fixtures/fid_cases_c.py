"""
Role: The PDF and DOCX halves of the FID fixture battery — `fid-001..013` (pdf) and
      `fid-014..026` (docx). Each case renders one synthetic ORIGINAL from `fid_cases_b` and
      pairs it with the expectation derived from that same ORIGINAL, never from an extractor
      (contract R12, section 10.5).
Used by: `fid_cases.py` (concatenates every half into `build_fid_cases()`).
Depends on: `fid_cases_a` (record vocabulary + `expectation_for_doc` + `fid_case`),
            `fid_cases_b` (the ORIGINAL library), `fid_builders` (the PDF assembler),
            `fid_builders_b` (the docx assembler). Nothing from `backend/app/` or
            `backend/tests/`.
Key invariants:
  - PDF ORIGINALS are Latin-script by construction (WinAnsi base font, no embedded font); the
    Bulgarian half of the battery therefore lives in the docx, xlsx, tnef, html, rtf, text and
    encoding batteries, never here in `pdf`.
  - THREE pdf cases and THREE docx cases carry `taxonomy_complete=True`: ordered paragraphs and
    list items, link label -> destination pairs, a real page or column boundary, and table tuples
    with header association — the contract 10.5 requirement for every format that can carry
    tables, links or multi-column layout.
  - `fid-003` is the MULTI-COLUMN case: its first page's blocks are painted so that a reader
    ordering text by vertical position alone interleaves the two columns. The expectation names
    the ORIGINAL's reading order (left column top to bottom, then right column, then page two), so
    that interleaving FAILS.
  - Content types are the ones the ingest path actually dispatches on: `application/pdf` and the
    OOXML wordprocessing type. A case never declares a type in order to dodge a dispatch branch.
"""

from __future__ import annotations

from tools.mem01_verify.fixtures.fid_builders import (
    pdf_from_doc,
    pdf_two_column_then_doc,
    pdf_two_page_from_doc,
)
from tools.mem01_verify.fixtures.fid_builders_b import build_docx
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
    EN_REPORT,
    LATIN_ORIGINALS,
    TWO_COLUMN_LEFT,
    TWO_COLUMN_RIGHT,
    boundary_sequence,
)

PDF_CONTENT_TYPE = "application/pdf"
DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _two_page_expectation(spec: DocSpec) -> FidExpectation:
    """The full-taxonomy expectation of a two-page rendering of one ORIGINAL."""
    return expectation_for_doc(
        spec,
        taxonomy_complete=True,
        forbidden=COLLAPSED_AMOUNTS,
        extra_sequences=(boundary_sequence(spec),),
    )


def _two_column_expectation(spec: DocSpec) -> FidExpectation:
    """The full-taxonomy expectation of a two-column layout page followed by the ORIGINAL's page.

    Reading order: the left layout column top to bottom, then the right layout column, then page
    two's paragraphs, list items, table, links and trailing paragraphs. Ordering the layout page's
    text by vertical position alone interleaves the two columns, which the sequence rejects.
    """
    layout = tuple(
        FidUnit(f"c1b{index}", "layout_block", text, page=1, layout_column=1)
        for index, text in enumerate(TWO_COLUMN_LEFT, start=1)
    ) + tuple(
        FidUnit(f"c2b{index}", "layout_block", text, page=1, layout_column=2)
        for index, text in enumerate(TWO_COLUMN_RIGHT, start=1)
    )
    base = expectation_for_doc(spec, taxonomy_complete=True, forbidden=COLLAPSED_AMOUNTS)
    crossing = (*(unit.unit_id for unit in layout), base.units[0].unit_id)
    return FidExpectation(
        units=layout + base.units,
        ordered_sequences=base.ordered_sequences + (crossing,),
        row_groups=base.row_groups,
        link_pairs=base.link_pairs,
        negation_guards=base.negation_guards,
        forbidden=base.forbidden,
        taxonomy_complete=True,
    )


def _pdf_cases() -> tuple[FidCase, ...]:
    """The pdf half: three full-taxonomy documents (two paged, one multi-column), ten singles."""
    cases: list[FidCase] = [
        fid_case(
            "fid-001",
            "full unit taxonomy in pdf: ordered paragraphs and list items on page one, the "
            "settlement table with its header association and the portal link on page two; the "
            "NOT-paid cell pins the negation and the NBSP amount pins the separator",
            "pdf",
            PDF_CONTENT_TYPE,
            pdf_two_page_from_doc(EN_INVOICE),
            _two_page_expectation(EN_INVOICE),
        ),
        fid_case(
            "fid-002",
            "full unit taxonomy in pdf, second document: planned vs delivered counts must stay "
            "under their own headers across the page boundary",
            "pdf",
            PDF_CONTENT_TYPE,
            pdf_two_page_from_doc(EN_REPORT),
            _two_page_expectation(EN_REPORT),
        ),
        fid_case(
            "fid-003",
            "full unit taxonomy in pdf with a MULTI-COLUMN page: page one is a two-column layout "
            "whose reading order is the left column top to bottom then the right column (ordering "
            "by vertical position alone interleaves them); page two carries the ordered "
            "paragraphs and list items, the table with its header association and the link",
            "pdf",
            PDF_CONTENT_TYPE,
            pdf_two_column_then_doc(EN_REPORT, TWO_COLUMN_LEFT, TWO_COLUMN_RIGHT),
            _two_column_expectation(EN_REPORT),
        ),
    ]
    for index, (origin, spec) in enumerate(LATIN_ORIGINALS[2:], start=4):
        cases.append(
            fid_case(
                f"fid-{index:03d}",
                f"pdf single page — {origin}",
                "pdf",
                PDF_CONTENT_TYPE,
                pdf_from_doc(spec),
                expectation_for_doc(spec, forbidden=COLLAPSED_AMOUNTS),
            )
        )
    return tuple(cases)


def _docx_taxonomy_expectation(spec: DocSpec) -> FidExpectation:
    """Full-taxonomy expectation for a docx whose tables start on a second page."""
    return expectation_for_doc(
        spec,
        taxonomy_complete=True,
        forbidden=COLLAPSED_AMOUNTS,
        extra_sequences=(boundary_sequence(spec),),
    )


def _docx_cases() -> tuple[FidCase, ...]:
    """The docx half: three full-taxonomy documents (two Bulgarian, one English), then ten more."""
    taxonomy_specs = (
        ("фактура на кирилица с page break преди таблицата", CYRILLIC_ORIGINALS[0][1]),
        ("протокол на кирилица с отрицание в таблицата след page break", CYRILLIC_ORIGINALS[1][1]),
        ("English invoice with a page break before the settlement table", EN_INVOICE),
    )
    cases: list[FidCase] = []
    for index, (origin, spec) in enumerate(taxonomy_specs, start=14):
        cases.append(
            fid_case(
                f"fid-{index:03d}",
                "full unit taxonomy in docx — "
                f"{origin}: ordered paragraphs and list items, a real external hyperlink "
                "relationship, a page boundary and table tuples with header association",
                "docx",
                DOCX_CONTENT_TYPE,
                build_docx(spec, page_break_before_tables=True),
                _docx_taxonomy_expectation(spec),
            )
        )
    remaining = [*CYRILLIC_ORIGINALS[2:], *LATIN_ORIGINALS[1:]]
    for index, (origin, spec) in enumerate(remaining[:10], start=17):
        cases.append(
            fid_case(
                f"fid-{index:03d}",
                f"docx single flow — {origin}",
                "docx",
                DOCX_CONTENT_TYPE,
                build_docx(spec),
                expectation_for_doc(spec, forbidden=COLLAPSED_AMOUNTS),
            )
        )
    return tuple(cases)


def build_pdf_cases() -> tuple[FidCase, ...]:
    """Build the pdf half of the battery (`fid-001` .. `fid-013`)."""
    return _pdf_cases()


def build_docx_cases() -> tuple[FidCase, ...]:
    """Build the docx half of the battery (`fid-014` .. `fid-026`)."""
    return _docx_cases()
