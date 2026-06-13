"""
Role: docx (OOXML WordprocessingML) attachment text extraction (design §2.4) — python-docx
      primary (body paragraphs + tables in DOCUMENT ORDER via Document.iter_inner_content(),
      tables pipe-serialized through the SAME serialize_table as PDF tables), with a stdlib
      zipfile + word/document.xml XML-harvest emergency fallback for OOXML zips python-docx
      rejects. Headers/footers are DELIBERATELY skipped (a §2.4 divergence): per-page running
      headers/footers are boilerplate noise (letterheads, page numbers, confidentiality footers)
      that would repeat into search/embeddings without adding content.
Used by: the IMAP connector's attachment_extractor (dispatches the
         application/vnd.openxmlformats-officedocument.wordprocessingml.document content type);
         scripts.backfill_attachment_extraction (via the seam). The arrow points IN — this module
         imports nothing back from any connector.
Depends on: python-docx (MIT; lxml BSD-3-Clause underneath) — verified GPL-free; sibling extraction
            modules only: app.connectors.extraction.extraction_result (the result contract),
            .common (serialize_table — ONE table serializer for every extractor, never
            re-implemented — MAX_EXTRACTED_CHARS, the shared stored-text cap, + package_version),
            .ooxml (is_ole_container + validate_ooxml_package — the SHARED OOXML zip-bomb battery
            every OOXML format inherits, lifted out so docx + xlsx never carry two copies),
            .text_sanitize (sanitize_body_text — the SINGLE stored-text sanitization source, shared
            with email bodies and PDF text); stdlib zipfile + xml.etree (the emergency fallback
            only). Imports NOTHING from any specific connector (connector-agnostic).
Key invariants:
  - extract_docx_text NEVER raises: every failure (zipfile/python-docx/lxml raise diverse
    internals on malformed payloads) degrades to an ExtractionResult; a final catch-all guards
    even our own bugs.
  - Table-cell text appears EXACTLY ONCE: iter_inner_content() yields top-level Paragraph and
    Table blocks in body order — cell paragraphs are reached only through their Table block,
    never doubled by a separate document.paragraphs pass — and MERGED cells (row.cells repeats
    the merge-origin per spanned grid slot) are emitted once, '' for the other slots.
  - PASSWORD-PROTECTED OOXML arrives as an OLE compound file (magic D0 CF 11 E0), not a zip
    (ECMA-376 encryption wraps the package) → `encrypted` with detail
    'ole-wrapped (password-protected)'. No password handling at MVP (design §2.3 policy).
  - Zip-bomb posture lives in extraction.ooxml (validate_ooxml_package — the shared battery: EOCD
    pre-gate, member-count bound, decompressed-expansion raw-bytes bound, and the DOM-parsed XML
    parts bound resolved by suffix AND [Content_Types].xml content-type disguise). docx keeps its
    MAX_* ceilings as module names so its guard tests can monkeypatch them per-format and passes
    them in; the OOM rationale (tiny-element WordprocessingML inflates ~15x its bytes in lxml tree
    RSS) and the media-headroom note are documented in ooxml.py.
  - No mid-loop char bail (unlike pdf.py's page loop): python-docx parses the WHOLE XML tree at
    Document() open, so a paragraph-loop bail saves nothing — the XML-parts bound is the real
    parse-memory bound, and the post-hoc MAX_EXTRACTED_CHARS cap (→ `truncated`, capped text
    STORED) is the storage bound.
  - `detail` carries exception CLASS NAMES / fixed phrases only — never str(exc) (lxml/zipfile
    messages can embed payload fragments) and never document content. python-docx and lxml were
    LIVE-CHECKED to emit zero log records on parse failure (2026-06-12, this module's leak
    regression test pins it) — no vendor-logger muting needed, unlike pypdf/pdfminer in pdf.py.
  - The emergency XML harvest uses stdlib ElementTree on the size-bounded word/document.xml only;
    expat ≥2.4 (bundled with every supported CPython) has billion-laughs amplification protection
    built in, and ElementTree never resolves external entities.
  - The caller (the seam) enforces the global size ceiling BEFORE this module parses anything.
"""

from __future__ import annotations

import logging
import zipfile
from io import BytesIO
from xml.etree import ElementTree

from docx import Document
from docx.table import Table

from app.connectors.extraction.common import (
    MAX_EXTRACTED_CHARS,
    serialize_table,
)
from app.connectors.extraction.common import (
    package_version as _package_version,
)
from app.connectors.extraction.extraction_result import (
    STATUS_CORRUPT,
    STATUS_EMPTY,
    STATUS_ENCRYPTED,
    STATUS_EXTRACTED,
    STATUS_TRUNCATED,
    ExtractionResult,
)
from app.connectors.extraction.ooxml import is_ole_container, validate_ooxml_package
from app.connectors.extraction.text_sanitize import sanitize_body_text

logger = logging.getLogger(__name__)

# The member the stdlib FALLBACK harvests. Deliberately NOT a package gate: OPC resolves the
# main document part via _rels/.rels, and python-docx parses legitimate packages whose main part
# has another name (Word's 'document2.xml' save quirk, non-Word producers) - pre-empting it
# falsely stamped valid documents `corrupt` (2026-06-12 review). A package python-docx rejects
# AND lacking this literal member degrades to `corrupt` in the fallback's own except.
_DOCUMENT_XML = "word/document.xml"

# The shared OOXML zip-bomb bounds (lifted to extraction.ooxml — every OOXML format is a zip and
# inherits the same defenses). Re-bound here as module names so docx's guard tests can monkeypatch
# them per-format; validate_ooxml_package reads whatever values these hold at call time. See
# ooxml.py for the measured rationale behind each ceiling.
MAX_ZIP_MEMBERS = 4096
MAX_CENTRAL_DIR_BYTES = 2 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_XML_PARTS_BYTES = 10 * 1024 * 1024
MAX_CONTENT_TYPES_BYTES = 1 * 1024 * 1024

# WordprocessingML namespace, pre-braced for ElementTree tag matching (the fallback harvest).
_WORDML = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

_PYTHON_DOCX = "python-docx"
# The emergency stdlib path has no package version — hand-bump when its rules change so a
# version-aware backfill can target rows harvested under older rules.
_XML_FALLBACK_NAME = "docx-zip-xml"
_XML_FALLBACK_VERSION = "1"


def extract_docx_text(payload: bytes) -> ExtractionResult:
    """Extract a docx's body text; never raises (every failure degrades to a status).

    Pipeline: OLE-magic probe (password-protected OOXML → `encrypted`) → zip validation
    (non-zip/broken-zip → `corrupt`; decompressed-expansion bound → `corrupt`
    'zip-expansion-bound'; member-count bound → `corrupt` 'zip-member-bound';
    DOM-parsed-members bound → `corrupt` 'xml-expansion-bound') → python-docx body traversal in
    document order (paragraphs as lines, tables pipe-serialized via the shared serialize_table;
    headers/footers skipped as boilerplate) → on a python-docx crash, the stdlib
    zipfile + ElementTree harvest of word/document.xml (w:t text, paragraph breaks per w:p;
    design §2.4's emergency path) → both engines failing → `corrupt` with the two exception
    class names in `detail`. Parsed-but-textless → `empty`; text over MAX_EXTRACTED_CHARS →
    `truncated` with the capped text STORED.

    Args:
        payload: the raw docx bytes (the seam has already enforced the global size ceiling).

    Returns:
        An ExtractionResult; text is non-None only for the text-bearing statuses
        (extracted / truncated).
    """
    try:
        return _extract(payload)
    except Exception as unexpected:  # the seam's NEVER-raise contract: even our bugs degrade
        # Class name ONLY - no exc_info: a formatted traceback ends with "Class: str(exc)",
        # and library exception strings can embed payload fragments (2026-06-12 review).
        logger.warning("docx extraction: unexpected failure (%s)", type(unexpected).__name__)
        return ExtractionResult(
            None, STATUS_CORRUPT, detail=f"unexpected:{type(unexpected).__name__}"
        )


def _extract(payload: bytes) -> ExtractionResult:
    """The real pipeline (see extract_docx_text); may raise — the public wrapper degrades."""
    if is_ole_container(payload):
        return ExtractionResult(
            None,
            STATUS_ENCRYPTED,
            detail="ole-container (password-protected docx or mislabeled legacy .doc)",
        )
    package_verdict = validate_ooxml_package(
        payload,
        max_zip_members=MAX_ZIP_MEMBERS,
        max_central_dir_bytes=MAX_CENTRAL_DIR_BYTES,
        max_decompressed_bytes=MAX_DECOMPRESSED_BYTES,
        max_xml_parts_bytes=MAX_XML_PARTS_BYTES,
        max_content_types_bytes=MAX_CONTENT_TYPES_BYTES,
    )
    if package_verdict is not None:
        return package_verdict
    try:
        body_text = _read_with_python_docx(payload)
    except Exception as docx_error:  # python-docx/lxml raise diverse internals on odd packages
        return _zip_xml_fallback(payload, docx_error)
    text = sanitize_body_text(body_text)
    if not text:
        return ExtractionResult(
            None,
            STATUS_EMPTY,
            extractor_name=_PYTHON_DOCX,
            extractor_version=_package_version(_PYTHON_DOCX),
        )
    return _text_result(text, _PYTHON_DOCX, _package_version(_PYTHON_DOCX))


def _read_with_python_docx(payload: bytes) -> str:
    """Primary pass: body blocks in DOCUMENT ORDER — paragraphs as lines, tables pipe-rows.

    Document.iter_inner_content() (python-docx ≥1.1, verified on 1.2.0) yields the body's
    top-level Paragraph and Table blocks in their actual XML order, so a paragraph→table→
    paragraph document round-trips in sequence and table-cell text appears exactly once (cell
    paragraphs are reached only through their Table). Headers/footers are section parts and
    never appear in the body iteration — the deliberate boilerplate skip. Returns '' when no
    block carries any non-whitespace content.
    """
    document = Document(BytesIO(payload))
    chunks: list[str] = []
    for block in document.iter_inner_content():
        if isinstance(block, Table):
            rendered = serialize_table(_table_rows_once(block))
        else:
            rendered = block.text
        if rendered.strip():
            chunks.append(rendered)
    return "\n".join(chunks)


def _table_rows_once(table: Table) -> list[list[str]]:
    """A table's cell texts with every MERGED cell emitted exactly once.

    row.cells returns the merge-ORIGIN cell repeated for every grid slot it spans (horizontally
    per row, vertically via vMerge across rows) - naive serialization repeats 'Summary' once per
    spanned column/row (2026-06-12 review). Tracked by the identity of the underlying `w:tc`
    element (cell._tc - python-docx's stable handle for the merged region), the origin slot
    keeps the text and every other spanned slot serializes as ''.
    """
    # The ELEMENTS themselves are held in the set — not their id()s: lxml node proxies are
    # transient, so a bare id() outlives its proxy and a GC-reused address falsely dedupes an
    # UNRELATED cell (live-caught: 'Bolts' blanked). Holding the element keeps its proxy alive,
    # and lxml then returns the SAME object for the same node (identity hashing is correct).
    seen_tc_elements: set[object] = set()
    rows: list[list[str]] = []
    for row in table.rows:
        cells: list[str] = []
        for cell in row.cells:
            tc_element = cell._tc
            cells.append("" if tc_element in seen_tc_elements else cell.text)
            seen_tc_elements.add(tc_element)
        rows.append(cells)
    return rows


def _zip_xml_fallback(payload: bytes, docx_error: Exception) -> ExtractionResult:
    """python-docx crashed on a real zip — harvest word/document.xml directly (design §2.4).

    The emergency path for OOXML zips python-docx rejects (e.g. a package missing
    [Content_Types].xml): stdlib zipfile + ElementTree, one line per w:p (its descendant w:t
    texts concatenated — table-cell paragraphs included exactly once, in document order; no
    table serialization on this degraded path). Both engines failing → `corrupt` with the two
    exception class names; parsed-but-textless → `empty`.
    """
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            document_xml = archive.read(_DOCUMENT_XML)
        harvested = _harvest_wordml_text(document_xml)
    except Exception as fallback_error:
        return ExtractionResult(
            None,
            STATUS_CORRUPT,
            detail=(
                f"python-docx:{type(docx_error).__name__} "
                f"xml-fallback:{type(fallback_error).__name__}"
            ),
        )
    text = sanitize_body_text(harvested)
    if not text:
        return ExtractionResult(
            None,
            STATUS_EMPTY,
            detail=f"python-docx:{type(docx_error).__name__} (xml fallback parsed, no text)",
            extractor_name=_XML_FALLBACK_NAME,
            extractor_version=_XML_FALLBACK_VERSION,
        )
    return _text_result(
        text,
        _XML_FALLBACK_NAME,
        _XML_FALLBACK_VERSION,
        detail=f"python-docx:{type(docx_error).__name__} (xml fallback)",
    )


def _harvest_wordml_text(document_xml: bytes) -> str:
    """Minimal WordprocessingML text harvest: each w:p (anywhere — body or table cell) becomes
    one line of its concatenated descendant w:t texts; empty paragraphs are dropped."""
    root = ElementTree.fromstring(document_xml)
    paragraphs = (
        "".join(node.text for node in paragraph.iter(f"{_WORDML}t") if node.text)
        for paragraph in root.iter(f"{_WORDML}p")
    )
    return "\n".join(line for line in paragraphs if line.strip())


def _text_result(
    text: str, extractor_name: str, extractor_version: str, detail: str | None = None
) -> ExtractionResult:
    """Wrap sanitized text as `extracted` — or `truncated` when it exceeds MAX_EXTRACTED_CHARS
    (the capped text IS stored; the cap detail outranks any fallback-provenance detail)."""
    if len(text) <= MAX_EXTRACTED_CHARS:
        return ExtractionResult(
            text,
            STATUS_EXTRACTED,
            detail=detail,
            extractor_name=extractor_name,
            extractor_version=extractor_version,
        )
    return ExtractionResult(
        text[:MAX_EXTRACTED_CHARS],
        STATUS_TRUNCATED,
        detail=f"capped from {len(text)} chars",
        extractor_name=extractor_name,
        extractor_version=extractor_version,
    )


