"""
Role: In-memory builders for the FID synthetic ORIGINALS — part 1: the PDF assembler (a valid
      PDF with computed xref offsets, absolute text placement, WinAnsi text encoding, real
      `/Link` URI annotations, real page boundaries and a real two-column layout) plus the row
      and list markers every flat renderer shares. A builder turns the ORIGINAL SPEC into bytes;
      it never inspects, imports or runs an extractor.
Used by: `fid_cases_c.py` (the pdf cases), `fid_cases_d.py` (the pdf embedded in a TNEF
         container), `fid_builders_c.py` (the rtf/html/plain-text renderers reuse the shared
         separators).
Depends on: stdlib only (`dataclasses`, `collections.abc`), `tools.mem01_verify.exceptions`
            (`FixtureError`, wave 1) and the sibling `fid_cases_a` for the `DocSpec` vocabulary.
            Nothing from `backend/app/`, nothing from `backend/tests/` (the shape of the PDF
            assembler is adapted from the extractor tests' in-memory conftest builder, copied
            rather than imported).
Key invariants:
  - BYTE-DETERMINISM: every builder is a pure function of its arguments, so one ORIGINAL always
    renders to the same bytes. (The OOXML containers need active help for that; it lives in
    `fid_builders_b.deterministic_zip`.)
  - The PDF is VALID: the xref offsets are computed from the assembled body, so a strict reader
    parses it without error recovery. Text is painted with an absolute text matrix per placement,
    so page and column geometry is real, not implied.
  - PDF text rides in WinAnsi (latin-1) — the base-14 Helvetica encoding. Cyrillic cannot be
    expressed without embedding a font, so PDF ORIGINALS are Latin-script by construction and
    `PdfEncodingError` is raised rather than silently mangling a scalar.
  - Every builder here lays the ORIGINAL out in the ONE order the specification fixes
    (paragraphs, list items, tables as caption/header/rows, link labels, trailing paragraphs) —
    the same order `expectation_for_doc` derives its constraints from. `CELL_SEPARATOR` and
    `BULLET_MARKER` are shared with the flat renderers so one row reads the same everywhere.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tools.mem01_verify.exceptions import FixtureError
from tools.mem01_verify.fixtures.fid_cases_a import DocSpec

#: Separator between the cells of one row in a flat-text render of a table.
CELL_SEPARATOR = " | "

#: List-item marker in a flat-text render (the unit literal stays a substring of the line).
BULLET_MARKER = "- "


class FidFixtureBuildError(FixtureError):
    """A synthetic ORIGINAL could not be assembled from its spec."""


class PdfEncodingError(FidFixtureBuildError):
    """A PDF ORIGINAL was asked to paint a scalar the WinAnsi base font cannot express."""


@dataclass(frozen=True, slots=True)
class TextPlacement:
    """One absolutely placed text run on a PDF page (user-space points, origin bottom-left)."""

    x: int
    y: int
    text: str


@dataclass(frozen=True, slots=True)
class PdfLinkAnnotation:
    """A `/Link` annotation of the ORIGINAL: a clickable rectangle carrying a URI destination."""

    x0: int
    y0: int
    x1: int
    y1: int
    uri: str


def escape_pdf_text(text: str) -> bytes:
    """Encode one literal for a PDF string operand, escaping the string delimiters.

    Args:
        text: the literal scalars to paint.

    Returns:
        The WinAnsi (latin-1) bytes with `\\`, `(` and `)` escaped.

    Raises:
        PdfEncodingError: the text contains a scalar outside the WinAnsi base-font encoding.
    """
    escaped = text.replace("\\", "\\\\").replace("(", r"\(").replace(")", r"\)")
    try:
        return escaped.encode("latin-1")
    except UnicodeEncodeError as encode_error:
        raise PdfEncodingError(
            f"non-WinAnsi scalar in PDF fixture text: {text!r}"
        ) from encode_error


def _content_stream(placements: Sequence[TextPlacement], font_size: int) -> bytes:
    """Assemble one page's content stream: one absolute text matrix + Tj per placement."""
    parts = [b"BT /F1 " + str(font_size).encode() + b" Tf\n"]
    for placement in placements:
        parts.append(f"1 0 0 1 {placement.x} {placement.y} Tm ".encode())
        parts.append(b"(" + escape_pdf_text(placement.text) + b") Tj\n")
    parts.append(b"ET")
    return b"".join(parts)


def build_pdf(
    pages: Sequence[Sequence[TextPlacement]],
    annotations: Sequence[PdfLinkAnnotation] = (),
    font_size: int = 11,
    annotation_page: int = 0,
) -> bytes:
    """Assemble a valid PDF with one page per placement list and correct xref offsets.

    Args:
        pages: one entry per page; each entry is the page's absolutely-placed text runs.
        annotations: `/Link` URI annotations of the ORIGINAL's links.
        font_size: point size of the single Helvetica/WinAnsi font resource.
        annotation_page: 0-based index of the page the annotations belong to — it must be the page
            whose text runs the annotation rectangles were computed against.

    Returns:
        The complete PDF bytes.

    Raises:
        FidFixtureBuildError: no pages were given.
        PdfEncodingError: a placement carries a scalar the base font cannot express.
    """
    if not pages:
        raise FidFixtureBuildError("a PDF ORIGINAL needs at least one page")
    page_count = len(pages)
    font_number = 3 + 2 * page_count
    annot_numbers = [font_number + 1 + index for index in range(len(annotations))]
    kids = " ".join(f"{3 + 2 * index} 0 R" for index in range(page_count))

    bodies: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode(),
    ]
    for page_index, placements in enumerate(pages):
        annots = ""
        if page_index == annotation_page and annot_numbers:
            annots = " /Annots [" + " ".join(f"{n} 0 R" for n in annot_numbers) + "]"
        bodies.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {3 + 2 * page_index + 1} 0 R "
                f"/Resources << /Font << /F1 {font_number} 0 R >> >>{annots} >>"
            ).encode()
        )
        stream = _content_stream(placements, font_size)
        bodies.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    bodies.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    for annotation in annotations:
        bodies.append(
            (
                f"<< /Type /Annot /Subtype /Link /Border [0 0 0] "
                f"/Rect [{annotation.x0} {annotation.y0} {annotation.x1} {annotation.y1}] "
                f"/A << /S /URI /URI ("
            ).encode()
            + escape_pdf_text(annotation.uri)
            + b") >> >>"
        )
    return _assemble_pdf_objects(bodies)


def _assemble_pdf_objects(bodies: Sequence[bytes]) -> bytes:
    """Serialize numbered PDF objects with a correct classic xref table and trailer."""
    document = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for object_number, body in enumerate(bodies, start=1):
        offsets.append(len(document))
        document += f"{object_number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_position = len(document)
    entry_count = len(bodies) + 1
    document += f"xref\n0 {entry_count}\n".encode()
    document += b"0000000000 65535 f \n"
    for offset in offsets:
        document += f"{offset:010d} 00000 n \n".encode()
    document += (
        f"trailer\n<< /Size {entry_count} /Root 1 0 R >>\nstartxref\n{xref_position}\n%%EOF\n"
    ).encode()
    return bytes(document)


def pdf_from_doc(spec: DocSpec, *, start_y: int = 740, leading: int = 18) -> bytes:
    """Paint one ORIGINAL onto a single PDF page, tables cell-by-cell at real column offsets.

    Every table cell becomes its own placement at the column's x offset, so a reader that loses
    the row/column geometry loses it against a genuinely two-dimensional original. Each link
    label is painted as text AND carries a `/Link` annotation with the destination URI.

    Args:
        spec: the ORIGINAL (Latin-script only — see the module invariants).
        start_y: baseline of the first line.
        leading: vertical distance between baselines.

    Returns:
        The complete PDF bytes.
    """
    placements: list[TextPlacement] = []
    annotations: list[PdfLinkAnnotation] = []
    y = start_y
    for text in [*spec.paragraphs, *(BULLET_MARKER + b for b in spec.bullets)]:
        placements.append(TextPlacement(60, y, text))
        y -= leading
    for table in spec.tables:
        placements.append(TextPlacement(60, y, table.caption))
        y -= leading
        for row_values in [table.headers, *table.rows]:
            for column_index, value in enumerate(row_values):
                placements.append(TextPlacement(60 + 105 * column_index, y, value))
            y -= leading
    for link in spec.links:
        placements.append(TextPlacement(60, y, link.label))
        annotations.append(PdfLinkAnnotation(58, y - 3, 300, y + 12, link.destination))
        y -= leading
    for text in spec.trailing:
        placements.append(TextPlacement(60, y, text))
        y -= leading
    return build_pdf([placements], annotations)


def pdf_two_page_from_doc(spec: DocSpec, *, start_y: int = 740, leading: int = 18) -> bytes:
    """Paint one ORIGINAL across TWO pages: narrative on page 1, tables/links/trailing on page 2.

    The split makes the page boundary a real property of the ORIGINAL, so the reading order across
    pages is a constraint the expectation can name rather than an accident of the flow.

    Args:
        spec: the ORIGINAL (Latin-script only).
        start_y: baseline of each page's first line.
        leading: vertical distance between baselines.

    Returns:
        The complete two-page PDF bytes.
    """
    first: list[TextPlacement] = []
    y = start_y
    for text in [*spec.paragraphs, *(BULLET_MARKER + b for b in spec.bullets)]:
        first.append(TextPlacement(60, y, text))
        y -= leading
    second: list[TextPlacement] = []
    annotations: list[PdfLinkAnnotation] = []
    y = start_y
    for table in spec.tables:
        second.append(TextPlacement(60, y, table.caption))
        y -= leading
        for row_values in [table.headers, *table.rows]:
            for column_index, value in enumerate(row_values):
                second.append(TextPlacement(60 + 105 * column_index, y, value))
            y -= leading
    for link in spec.links:
        second.append(TextPlacement(60, y, link.label))
        annotations.append(PdfLinkAnnotation(58, y - 3, 300, y + 12, link.destination))
        y -= leading
    for text in spec.trailing:
        second.append(TextPlacement(60, y, text))
        y -= leading
    return build_pdf([first, second], annotations, annotation_page=1)


def pdf_two_column_then_doc(
    spec: DocSpec,
    left: Sequence[str],
    right: Sequence[str],
    *,
    start_y: int = 700,
    leading: int = 40,
) -> bytes:
    """Paint a two-column layout page followed by the ORIGINAL's own blocks on a second page.

    Page one is the multi-column layout: the blocks alternate in VERTICAL position between the two
    columns, so a reader ordering text by vertical position alone interleaves them. Page two
    carries the ORIGINAL's paragraphs, list items, table (cell by cell at real column offsets),
    links and trailing paragraphs. The result is one document that carries the whole unit taxonomy
    at once: ordered paragraphs and list items, link label/destination pairs, a real page AND
    column order, and table tuples with header association.

    Args:
        spec: the ORIGINAL rendered onto page two.
        left: the left layout column's blocks, top to bottom.
        right: the right layout column's blocks, top to bottom.
        start_y: baseline of the first block in each layout column.
        leading: vertical distance between the blocks of one layout column.

    Returns:
        The complete two-page PDF bytes.
    """
    layout = [TextPlacement(60, start_y - leading * i, t) for i, t in enumerate(left)]
    layout += [TextPlacement(330, start_y - 12 - leading * i, t) for i, t in enumerate(right)]
    body: list[TextPlacement] = []
    annotations: list[PdfLinkAnnotation] = []
    y = 740
    for text in [*spec.paragraphs, *(BULLET_MARKER + b for b in spec.bullets)]:
        body.append(TextPlacement(60, y, text))
        y -= 18
    for table in spec.tables:
        body.append(TextPlacement(60, y, table.caption))
        y -= 18
        for row_values in [table.headers, *table.rows]:
            for column_index, value in enumerate(row_values):
                body.append(TextPlacement(60 + 105 * column_index, y, value))
            y -= 18
    for link in spec.links:
        body.append(TextPlacement(60, y, link.label))
        annotations.append(PdfLinkAnnotation(58, y - 3, 300, y + 12, link.destination))
        y -= 18
    for text in spec.trailing:
        body.append(TextPlacement(60, y, text))
        y -= 18
    return build_pdf([layout, body], annotations, annotation_page=1)
