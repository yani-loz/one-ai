"""
Role: Unit tests for the attachment extraction seam — text/* (and text-shaped application/*)
      decode inline as `extracted` and pass through sanitize_body_text (the SINGLE sanitization
      source: CRLF→LF + C0 + surrogate strip); PDF dispatches to extractors.pdf; non-documents and
      not-yet-supported document formats get their honest skip/unsupported statuses; the global
      size ceiling; never raises. Pure, no I/O.
Used by: pytest (tests/connectors/imap/parsing).
Depends on: app.connectors.imap.parsing.attachment_extractor + .extraction_result + .models;
            the extractors conftest PDF builder (a real parseable PDF payload).
"""

from __future__ import annotations

import pytest

import app.connectors.imap.parsing.attachment_extractor as extractor_module
from app.connectors.imap.parsing.attachment_extractor import extract_text
from app.connectors.imap.parsing.extraction_result import (
    STATUS_EMPTY,
    STATUS_EXTRACTED,
    STATUS_SKIPPED_NONDOCUMENT,
    STATUS_SKIPPED_OVERSIZE,
    STATUS_UNSUPPORTED_FORMAT,
)
from app.connectors.imap.parsing.models import ParsedAttachment
from tests.connectors.imap.parsing.extractors.conftest import TEXT_PAGE_STREAM, build_pdf


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


def test_extract_text_pdf_dispatches_to_pdf_extractor() -> None:
    result = extract_text(_attachment("application/pdf", build_pdf([TEXT_PAGE_STREAM])))

    assert result.status == STATUS_EXTRACTED
    assert result.text == "[page 1]\nHello World"
    assert result.extractor_name == "pdfplumber"


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
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/rtf",
        "application/ms-tnef",
        "application/octet-stream",
    ],
)
def test_extract_text_unhandled_document_formats_return_unsupported_format(
    content_type: str,
) -> None:
    # Document-bearing formats without a Phase-A extractor: honest NULL + unsupported_format
    # (Phase B/C re-target exactly these rows via the backfill).
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
