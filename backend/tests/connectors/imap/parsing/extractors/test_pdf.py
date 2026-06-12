"""
Role: Unit tests for the PDF extractor — text extraction with page markers, table serialization,
      the §3.1 WHOLE-DOCUMENT text-preserving scanned classification (scanned_pending_ocr /
      extracted_partial_scanned), encrypted/corrupt/empty/truncated statuses, the two CPU bounds
      (char-cap early bail + MAX_PDF_PAGES), C0 + lone-surrogate sanitization, and the
      NEVER-raises property. Fixtures are hand-crafted in-memory PDFs (see conftest) — pure,
      no I/O, no real corpus.
Used by: pytest (tests/connectors/imap/parsing/extractors).
Depends on: app.connectors.imap.parsing.extractors.pdf, .extraction_result, the conftest builders.
"""

from __future__ import annotations

import pytest

import app.connectors.imap.parsing.extractors.pdf as pdf_module
from app.connectors.imap.parsing.extraction_result import (
    EXTRACTION_STATUSES,
    STATUS_CORRUPT,
    STATUS_EMPTY,
    STATUS_ENCRYPTED,
    STATUS_EXTRACTED,
    STATUS_EXTRACTED_PARTIAL_SCANNED,
    STATUS_PENDING,
    STATUS_SCANNED_PENDING_OCR,
    STATUS_TRUNCATED,
    ExtractionResult,
)
from app.connectors.imap.parsing.extractors.pdf import (
    extract_pdf_text,
    serialize_table,
)
from tests.connectors.imap.parsing.extractors.conftest import (
    EMPTY_PAGE_STREAM,
    IMAGE_PAGE_STREAM,
    TEXT_PAGE_STREAM,
    build_pdf,
    encrypt_pdf,
    text_page_stream,
)

# — text extraction —


def test_extract_pdf_text_single_text_page_returns_extracted_with_page_marker() -> None:
    payload = build_pdf([TEXT_PAGE_STREAM])

    result = extract_pdf_text(payload)

    assert result.status == STATUS_EXTRACTED
    assert result.text == "[page 1]\nHello World"


def test_extract_pdf_text_multi_page_marks_every_page_number() -> None:
    payload = build_pdf([text_page_stream("First page"), text_page_stream("Second page")])

    result = extract_pdf_text(payload)

    assert result.status == STATUS_EXTRACTED
    assert result.text is not None
    assert "[page 1]\nFirst page" in result.text
    assert "[page 2]\nSecond page" in result.text


def test_extract_pdf_text_success_records_pdfplumber_provenance() -> None:
    from importlib import metadata

    result = extract_pdf_text(build_pdf([TEXT_PAGE_STREAM]))

    assert result.extractor_name == "pdfplumber"
    assert result.extractor_version == metadata.version("pdfplumber")


# — table serialization (pdfplumber needs drawn rulings to detect tables, so the serializer is
#   unit-tested directly per the design's test note) —


def test_serialize_table_rows_joined_with_pipes() -> None:
    table = [["Item", "Qty"], ["Bolts", "12"]]

    rendered = serialize_table(table)

    assert rendered == "Item | Qty\nBolts | 12"


def test_serialize_table_none_cells_and_newlines_flattened() -> None:
    table = [["Total\nDue", None, "100"]]

    rendered = serialize_table(table)

    assert rendered == "Total Due |  | 100"


def test_serialize_table_all_blank_cells_returns_empty_string() -> None:
    table = [[None, ""], ["", None]]

    rendered = serialize_table(table)

    assert rendered == ""


# — scanned detect-and-mark (§3.1; whole-document, text-preserving; Phase A NEVER OCRs) —


def test_extract_pdf_text_image_only_page_returns_scanned_pending_ocr_with_null_text() -> None:
    payload = build_pdf([IMAGE_PAGE_STREAM], with_image=True)

    result = extract_pdf_text(payload)

    assert result.status == STATUS_SCANNED_PENDING_OCR
    assert result.text is None  # marked for Phase C OCR — never fake/garbage text


def test_extract_pdf_text_tiny_text_with_covering_image_keeps_text_as_partial_scanned() -> None:
    # 'Paid, thanks' = 12 chars: under the 20-char floor with a page-covering image the scan
    # SIGNAL fires — but the pypdf rescue reads the same 12 real chars, and text ANY engine can
    # read is never discarded (2026-06-12 Codex review). The covering image stays flagged for
    # Phase C via extracted_partial_scanned.
    speckle_page = b"q 612 0 0 792 0 0 cm /Im1 Do Q BT /F1 24 Tf 72 720 Td (Paid, thanks) Tj ET"
    payload = build_pdf([speckle_page], with_image=True)

    result = extract_pdf_text(payload)

    assert result.status == STATUS_EXTRACTED_PARTIAL_SCANNED
    assert result.text is not None and "Paid, thanks" in result.text


def test_extract_pdf_text_scanned_only_after_both_engines_find_no_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The order contract itself (2026-06-12 Codex review): an image-backed PDF where pdfplumber
    # reads nothing must consult the pypdf rescue BEFORE being classified scanned — the two
    # engines fail on different font pathologies, and a one-engine verdict would discard a
    # recoverable text layer (NULL stored, bytes dropped on the live path = permanent loss).
    from app.connectors.imap.parsing.extractors import pdf as pdf_module

    rescued = ExtractionResult(
        "recovered by the second engine", STATUS_EXTRACTED, extractor_name="pypdf"
    )
    monkeypatch.setattr(pdf_module, "_pypdf_text_rescue", lambda payload: rescued)
    payload = build_pdf([IMAGE_PAGE_STREAM], with_image=True)

    result = extract_pdf_text(payload)

    assert result.status == STATUS_EXTRACTED_PARTIAL_SCANNED
    assert result.text == "recovered by the second engine"


def test_extract_pdf_text_real_text_with_covering_image_returns_extracted() -> None:
    # ≥20 chars on a page WITH a covering image ⇒ born-digital text over a background image:
    # the page is not image-only, so the document is plainly extracted.
    text_over_image = (
        b"q 612 0 0 792 0 0 cm /Im1 Do Q "
        b"BT /F1 24 Tf 72 720 Td (A real paragraph of text, well over the probe.) Tj ET"
    )
    payload = build_pdf([text_over_image], with_image=True)

    result = extract_pdf_text(payload)

    assert result.status == STATUS_EXTRACTED
    assert result.text is not None and "real paragraph" in result.text


def test_extract_pdf_text_image_cover_pages_keep_later_text_as_partial_scanned() -> None:
    # The 2026-06-11 review case: a born-digital report behind 3 image-only cover pages. The old
    # leading-page probe discarded ALL extracted text; the whole-document verdict must STORE the
    # later pages' text and mark the image-only pages for Phase C OCR.
    later_page_text = "Quarterly results, discussed in real extracted text."
    cover_pages = [IMAGE_PAGE_STREAM, IMAGE_PAGE_STREAM, IMAGE_PAGE_STREAM]
    payload = build_pdf([*cover_pages, text_page_stream(later_page_text)], with_image=True)

    result = extract_pdf_text(payload)

    assert result.status == STATUS_EXTRACTED_PARTIAL_SCANNED
    assert result.text is not None and later_page_text in result.text  # text NEVER discarded
    assert "[page 4]" in result.text
    assert result.detail == "image_only_pages=3 pending OCR"


def test_extract_pdf_text_all_text_document_returns_extracted() -> None:
    payload = build_pdf(
        [
            text_page_stream("A full paragraph of perfectly ordinary text."),
            text_page_stream("And a second page with more of the same."),
        ]
    )

    result = extract_pdf_text(payload)

    assert result.status == STATUS_EXTRACTED
    assert result.text is not None and "second page" in result.text
    assert result.detail is None


def test_extract_pdf_text_no_text_no_images_returns_empty_with_null_text() -> None:
    payload = build_pdf([EMPTY_PAGE_STREAM])

    result = extract_pdf_text(payload)

    assert result.status == STATUS_EMPTY
    assert result.text is None  # honest NULL — page markers alone are not content


def test_extract_pdf_text_vector_drawn_zero_text_routes_to_ocr_queue() -> None:
    # EQ-1 (2026-06-12 quality audit): payroll/VAT/invoice PDFs draw their text as VECTOR
    # objects — no engine extracts characters, no raster image fires the covering signal.
    # 'empty' would exile them from every recovery path; they must reach the OCR queue.
    vector_rects = " ".join(f"{50 + i * 15} 700 12 20 re S" for i in range(30)).encode()
    payload = build_pdf([vector_rects])

    result = extract_pdf_text(payload)

    assert result.status == STATUS_SCANNED_PENDING_OCR
    assert result.text is None
    assert result.detail is not None and "vector/image content" in result.detail


def test_extract_pdf_text_inset_image_zero_text_routes_to_ocr_queue() -> None:
    # EQ-1's second face: a small INSET scan (no page-covering geometry) on a zero-text page —
    # any image at all on a text-free document is content pending OCR, never 'empty'.
    inset_image_stream = b"q 100 0 0 100 50 50 cm /Im1 Do Q"
    payload = build_pdf([inset_image_stream], with_image=True)

    result = extract_pdf_text(payload)

    assert result.status == STATUS_SCANNED_PENDING_OCR
    assert result.text is None


# — vendor-logger payload leak (2026-06-12 Codex review) —


def test_extract_pdf_text_corrupt_payload_bytes_never_reach_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # pypdf interpolates the payload's first bytes into its WARNING ("invalid pdf header:
    # b'SECRE'") — tenant content leaking into app logs. The module mutes the vendor loggers at
    # import; a misdeclared/corrupt attachment must degrade to a status with ZERO payload bytes
    # in any log record. (caplog only adjusts the ROOT level — the vendor loggers' own CRITICAL
    # threshold, the thing under test, stays in force.)
    import logging

    sensitive = b"SECRET TENANT CONTRACT: salary table follows, do not distribute " * 4

    with caplog.at_level(logging.DEBUG):
        result = extract_pdf_text(sensitive)

    assert result.status == STATUS_CORRUPT
    assert result.text is None
    leaked = [r for r in caplog.records if "SECRE" in r.getMessage()]
    assert leaked == []


# — encrypted (§2.3) —


def test_extract_pdf_text_user_password_returns_encrypted_with_null_text() -> None:
    payload = encrypt_pdf(build_pdf([TEXT_PAGE_STREAM]), user_password="secret")

    result = extract_pdf_text(payload)

    assert result.status == STATUS_ENCRYPTED
    assert result.text is None


def test_extract_pdf_text_owner_only_lock_decrypts_with_empty_password_and_extracts() -> None:
    # Owner-password-only locks open with the empty user password — the recipient holds the file.
    payload = encrypt_pdf(build_pdf([TEXT_PAGE_STREAM]), user_password="", owner_password="own")

    result = extract_pdf_text(payload)

    assert result.status == STATUS_EXTRACTED
    assert result.text is not None and "Hello World" in result.text


# — corrupt + the never-raises property —


def test_extract_pdf_text_garbage_bytes_returns_corrupt_with_exception_class_names() -> None:
    result = extract_pdf_text(b"%PDF-1.4 this is not a real pdf body")

    assert result.status == STATUS_CORRUPT
    assert result.text is None
    assert result.detail is not None
    assert "pdfplumber:" in result.detail and "pypdf:" in result.detail


def test_extract_pdf_text_corrupt_detail_carries_class_names_not_payload_content() -> None:
    # The payload's (potentially sensitive) content must never echo into the stored detail.
    marker = "TOP-SECRET-SALARY-TABLE"

    result = extract_pdf_text(f"%PDF-1.4 {marker}".encode())

    assert result.status == STATUS_CORRUPT
    assert marker not in (result.detail or "")


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\x00" * 64,
        b"not a pdf at all",
        build_pdf([TEXT_PAGE_STREAM])[: len(build_pdf([TEXT_PAGE_STREAM])) // 2],  # truncated
        build_pdf([TEXT_PAGE_STREAM]).replace(b"xref", b"XXXX"),  # broken xref table
        build_pdf([TEXT_PAGE_STREAM]).replace(b"/Type /Page ", b"/Type /Junk "),
    ],
)
def test_extract_pdf_text_mutated_payloads_never_raise(payload: bytes) -> None:
    result = extract_pdf_text(payload)

    assert isinstance(result, ExtractionResult)
    assert result.status in EXTRACTION_STATUSES and result.status != STATUS_PENDING


# — the two CPU bounds (char-cap early bail + MAX_PDF_PAGES; both → truncated) —


def test_extract_pdf_text_above_cap_truncates_and_stores_capped_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pdf_module, "MAX_EXTRACTED_CHARS", 12)
    payload = build_pdf([TEXT_PAGE_STREAM])  # "[page 1]\nHello World" = 20 chars

    result = extract_pdf_text(payload)

    assert result.status == STATUS_TRUNCATED
    assert result.text == "[page 1]\nHel"  # capped text IS stored
    assert result.detail == "capped from 20 chars"


def test_extract_pdf_text_char_cap_bails_out_before_parsing_later_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The early bail: once the materialized text exceeds the cap mid-loop, later pages are never
    # parsed. detail proves it — 'capped from 20 chars' counts page 1 ONLY (a post-hoc cap over a
    # fully-materialized two-page document would have counted both pages).
    monkeypatch.setattr(pdf_module, "MAX_EXTRACTED_CHARS", 12)
    payload = build_pdf([TEXT_PAGE_STREAM, text_page_stream("Second page never parsed")])

    result = extract_pdf_text(payload)

    assert result.status == STATUS_TRUNCATED
    assert result.text == "[page 1]\nHel"
    assert result.detail == "capped from 20 chars"  # page 2 never materialized


def test_extract_pdf_text_page_bound_stops_iteration_and_returns_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # MAX_PDF_PAGES is the CPU-budget proxy against pathological/quasi-hang PDFs: pages past the
    # bound are never read; text found by then is stored as truncated with a page-bounded detail.
    monkeypatch.setattr(pdf_module, "MAX_PDF_PAGES", 2)
    payload = build_pdf(
        [
            text_page_stream("Page one body text"),
            text_page_stream("Page two body text"),
            text_page_stream("Page three body text"),
        ]
    )

    result = extract_pdf_text(payload)

    assert result.status == STATUS_TRUNCATED
    assert result.detail == "page-bounded: stopped after 2 pages"
    assert result.text is not None
    assert "Page two" in result.text and "Page three" not in result.text


# — sanitization (sanitize_body_text: same rules as email bodies — storable as UTF-8, period) —


def test_extract_pdf_text_strips_c0_and_nul_from_engine_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A real CMap/broken-encoding PDF can yield raw C0 controls from pdfminer (a simple-font
    # fixture can't — Helvetica renders them as '(cid:N)' text), so the engine boundary is
    # stubbed: WHATEVER pdfplumber yields must pass the email-body C0/NUL rules before storage.
    monkeypatch.setattr(
        pdf_module,
        "_read_with_pdfplumber",
        lambda payload: pdf_module._PlumberDocument(
            "[page 1]\nHel\x07lo\x00 World\r\nnext", [], None
        ),
    )

    result = extract_pdf_text(b"%PDF-1.4 irrelevant, reader is stubbed")

    assert result.status == STATUS_EXTRACTED
    assert result.text == "[page 1]\nHello World\nnext"  # BEL + NUL stripped, CRLF → LF


def test_extract_pdf_text_strips_lone_surrogates_so_text_is_utf8_storable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pdfminer/pypdf emit LONE SURROGATES (U+D800–U+DFFF) from broken ToUnicode CMaps; they pass
    # C0 sanitization and then asyncpg raises UnicodeEncodeError at flush — the email becomes a
    # poison message. The seam must strip them and still report extracted.
    monkeypatch.setattr(
        pdf_module,
        "_read_with_pdfplumber",
        lambda payload: pdf_module._PlumberDocument(
            "[page 1]\nInvoice \ud83d total \udfff due", [], None
        ),
    )

    result = extract_pdf_text(b"%PDF-1.4 irrelevant, reader is stubbed")

    assert result.status == STATUS_EXTRACTED
    assert result.text == "[page 1]\nInvoice  total  due"  # surrogate-free
    result.text.encode("utf-8")  # must not raise — the storability guarantee asyncpg relies on
