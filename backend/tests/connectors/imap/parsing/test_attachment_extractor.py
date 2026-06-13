"""
Role: Unit tests for the attachment extraction seam — text/* (and text-shaped application/*)
      decode inline as `extracted` via the STRICT charset chain (EQ-3: utf-8 → cp1252 →
      windows-1251 → utf-8-replace) and pass through sanitize_body_text (the SINGLE sanitization
      source); text/html flattens through the email-body flattener and text/rtf +
      application/rtf strip through striprtf (EQ-4 — raw markup never stores as 'extracted');
      PDF dispatches to extraction.pdf, docx to extraction.docx, TNEF to extraction.tnef;
      non-documents and not-yet-supported document formats get their honest skip/unsupported
      statuses; the global size ceiling; never raises. Pure, no I/O.
Used by: pytest (tests/connectors/imap/parsing).
Depends on: app.connectors.imap.parsing.attachment_extractor + .models,
            app.connectors.extraction.extraction_result; the tests.connectors.extraction conftest
            builders (real parseable PDF/docx/TNEF payloads).
"""

from __future__ import annotations

import pytest

import app.connectors.imap.parsing.attachment_extractor as extractor_module
from app.connectors.extraction.extraction_result import (
    STATUS_EMPTY,
    STATUS_EXTRACTED,
    STATUS_SKIPPED_NONDOCUMENT,
    STATUS_SKIPPED_OVERSIZE,
    STATUS_UNSUPPORTED_FORMAT,
)
from app.connectors.imap.parsing.attachment_extractor import extract_text
from app.connectors.imap.parsing.models import ParsedAttachment
from tests.connectors.extraction.conftest import (
    TEXT_PAGE_STREAM,
    build_docx,
    build_pdf,
    build_tnef,
)


def _attachment(content_type: str, payload: bytes) -> ParsedAttachment:
    return ParsedAttachment(
        filename="f",
        content_type=content_type,
        size_bytes=len(payload),
        content_hash="x" * 64,
        is_inline=False,
        content_id=None,
        payload=payload,
    )


def test_extract_text_plain_text_returns_extracted_with_provenance() -> None:
    result = extract_text(_attachment("text/plain", b"hello world"))

    assert result.text == "hello world"
    assert result.status == STATUS_EXTRACTED
    assert result.extractor_name == "text-decode"
    assert result.extractor_version is not None


@pytest.mark.parametrize("content_type", ["application/json", "application/text", "text/csv"])
def test_extract_text_text_shaped_types_returns_extracted(content_type: str) -> None:
    result = extract_text(_attachment(content_type, b"k=1"))

    assert result.text == "k=1"
    assert result.status == STATUS_EXTRACTED


def test_extract_text_text_attachment_sanitized_like_email_bodies() -> None:
    # FIX: the text/* path used a bare NUL strip while the docstrings promised ONE sanitization
    # source — decoded text now flows through sanitize_body_text (CRLF→LF + C0 strip).
    result = extract_text(_attachment("text/plain", b"line one\r\nline\x07 two"))

    assert result.status == STATUS_EXTRACTED
    assert result.text == "line one\nline two"


def test_extract_text_text_attachment_only_control_chars_returns_empty() -> None:
    # A text attachment whose content sanitizes to NOTHING stores honest NULL with status empty.
    result = extract_text(_attachment("text/plain", b"\x07\x0b\x1f"))

    assert result.text is None
    assert result.status == STATUS_EMPTY


def test_extract_text_blank_text_payload_returns_empty_with_null_text() -> None:
    result = extract_text(_attachment("text/plain", b"   \r\n  "))

    assert result.text is None  # honest NULL, never ''
    assert result.status == STATUS_EMPTY


def test_extract_text_empty_payload_returns_empty() -> None:
    result = extract_text(_attachment("text/plain", b""))

    assert result.text is None
    assert result.status == STATUS_EMPTY


def test_extract_text_bad_utf8_does_not_raise() -> None:
    result = extract_text(_attachment("text/plain", b"ok \xff\xfe bytes"))

    assert result.status == STATUS_EXTRACTED
    assert result.text is not None and "ok" in result.text


# — EQ-3: the strict charset chain (utf-8 → cp1252 → windows-1251 → utf-8-replace) —


def test_extract_text_windows_1251_payload_recovered_losslessly() -> None:
    # The audit's EQ-3 case (9 rows / 564K U+FFFD chars, files verified windows-1251): bytes
    # that break BOTH utf-8 and cp1252 strict (0x90 is a cp1252 hole) must recover via the
    # windows-1251 link of the chain — zero replacement chars.
    original = "Договор № 5 — ОДОБРЕНО ђ"
    payload = original.encode("windows-1251")
    assert b"\x90" in payload  # the cp1252-undefined byte that forces the 1251 fallback

    result = extract_text(_attachment("text/plain", payload))

    assert result.status == STATUS_EXTRACTED
    assert result.text == original  # lossless — no U+FFFD anywhere
    assert "�" not in (result.text or "")
    assert result.extractor_name == "text-decode"
    assert result.extractor_version == "3"  # EQ-3 bumped the decoder version


def test_extract_text_cp1252_payload_recovered_before_replace_fallback() -> None:
    original = "café résumé — naïve"
    payload = original.encode("cp1252")  # invalid utf-8 (0xE9 etc.), valid cp1252

    result = extract_text(_attachment("text/plain", payload))

    assert result.status == STATUS_EXTRACTED
    assert result.text == original


def test_extract_text_undecodable_payload_falls_back_to_replace() -> None:
    # 0x90 breaks cp1252, 0x98 breaks windows-1251, the run breaks utf-8 — the final
    # errors='replace' link stores U+FFFD instead of raising (never-raise preserved).
    result = extract_text(_attachment("text/plain", b"a \x90 \x98 z"))

    assert result.status == STATUS_EXTRACTED
    assert result.text is not None and "�" in result.text
    assert result.text.startswith("a ") and result.text.endswith(" z")


# — EQ-4: markup attachments flatten — raw source must never store as 'extracted' —


def test_extract_text_html_attachment_flattened_not_raw_source() -> None:
    payload = (
        b"<html><head><style>body{color:red}</style></head>"
        b"<body><h1>Invoice 42</h1><p>Total: <b>100 EUR</b></p></body></html>"
    )

    result = extract_text(_attachment("text/html", payload))

    assert result.status == STATUS_EXTRACTED
    assert result.text is not None
    assert "Invoice 42" in result.text and "100 EUR" in result.text
    assert "<h1>" not in result.text and "<body" not in result.text  # no raw markup stored
    assert result.extractor_name == "html2text"  # the flattening engine is the provenance
    assert result.extractor_version is not None


def test_extract_text_html_attachment_charset_chain_applies_before_flatten() -> None:
    # EQ-3 + EQ-4 compose: a windows-1251 html file (0x90 forces the 1251 link) flattens to
    # clean Cyrillic text, not mojibake markup.
    payload = "<html><body><p>Договор ђ одобрен</p></body></html>".encode("windows-1251")

    result = extract_text(_attachment("text/html", payload))

    assert result.status == STATUS_EXTRACTED
    assert result.text is not None and "Договор ђ одобрен" in result.text
    assert "�" not in result.text


@pytest.mark.parametrize("content_type", ["text/rtf", "application/rtf"])
def test_extract_text_rtf_attachment_stripped_not_raw_source(content_type: str) -> None:
    payload = b"{\\rtf1\\ansi\\ansicpg1252\\deff0 Quarterly \\b report\\b0  attached.}"

    result = extract_text(_attachment(content_type, payload))

    assert result.status == STATUS_EXTRACTED
    assert result.text == "Quarterly report attached."
    assert "rtf1" not in result.text  # no control words stored
    assert result.extractor_name == "striprtf"
    assert result.extractor_version is not None


def test_extract_text_rtf_attachment_cp1251_escapes_decoded() -> None:
    payload = b"{\\rtf1\\ansi\\ansicpg1251\\deff0 \\'c4\\'ee\\'e3\\'ee\\'e2\\'ee\\'f0 2026}"

    result = extract_text(_attachment("text/rtf", payload))

    assert result.status == STATUS_EXTRACTED
    assert result.text == "Договор 2026"


def test_extract_text_html_attachment_flattening_to_blank_returns_empty() -> None:
    result = extract_text(_attachment("text/html", b"<html><body></body></html>"))

    assert result.text is None  # honest NULL, never '' and never the markup source
    assert result.status == STATUS_EMPTY


# — TNEF dispatch (design §2.9) —


def test_extract_text_tnef_dispatches_to_tnef_extractor() -> None:
    payload = build_tnef(body=b"TNEF carried body text")

    result = extract_text(_attachment("application/ms-tnef", payload))

    assert result.status == STATUS_EXTRACTED
    assert result.text == "TNEF carried body text"
    assert result.extractor_name == "tnefparse"
    assert result.detail == "body=plain embedded_files=0"


def test_extract_text_pdf_dispatches_to_pdf_extractor() -> None:
    result = extract_text(_attachment("application/pdf", build_pdf([TEXT_PAGE_STREAM])))

    assert result.status == STATUS_EXTRACTED
    assert result.text == "[page 1]\nHello World"
    assert result.extractor_name == "pdfplumber"


def test_extract_text_docx_dispatches_to_docx_extractor() -> None:
    payload = build_docx(["Hello from a docx"])
    content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    result = extract_text(_attachment(content_type, payload))

    assert result.status == STATUS_EXTRACTED
    assert result.text == "Hello from a docx"
    assert result.extractor_name == "python-docx"


@pytest.mark.parametrize(
    "content_type",
    ["image/png", "image/jpeg", "audio/mpeg", "video/mp4", "application/zip",
     "application/x-rar-compressed", "application/pkcs7-signature", "message/delivery-status"],
)
def test_extract_text_nondocument_types_return_skipped_nondocument(content_type: str) -> None:
    result = extract_text(_attachment(content_type, b"\x89PNG... binary"))

    assert result.text is None
    assert result.status == STATUS_SKIPPED_NONDOCUMENT


@pytest.mark.parametrize(
    "content_type",
    [
        "application/vnd.ms-excel",
        "application/octet-stream",
    ],
)
def test_extract_text_unhandled_document_formats_return_unsupported_format(
    content_type: str,
) -> None:
    # Document-bearing formats without an extractor yet: honest NULL + unsupported_format
    # (later phases re-target exactly these rows via the backfill). application/rtf and
    # application/ms-tnef LEFT this list in the TNEF slice (EQ-4 / design §2.9); octet-stream
    # stays until its sniff-dispatch slice (§2.10) lands.
    result = extract_text(_attachment(content_type, b"\xd0\xcf\x11\xe0 ole..."))

    assert result.text is None
    assert result.status == STATUS_UNSUPPORTED_FORMAT


def test_extract_text_oversize_payload_skipped_before_any_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The global ceiling applies to EVERY path — even a valid PDF is not parsed above it.
    monkeypatch.setattr(extractor_module, "MAX_PARSE_BYTES", 16)
    payload = build_pdf([TEXT_PAGE_STREAM])

    result = extract_text(_attachment("application/pdf", payload))

    assert result.text is None
    assert result.status == STATUS_SKIPPED_OVERSIZE
    assert result.detail == f"{len(payload)} bytes"


def test_extract_text_corrupt_pdf_payload_never_raises() -> None:
    result = extract_text(_attachment("application/pdf", b"%PDF-1.4 binary..."))

    assert result.text is None  # honest absent — and the seam did not raise
    assert result.status == "corrupt"
