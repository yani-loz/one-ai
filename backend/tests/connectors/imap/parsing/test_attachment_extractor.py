"""
Role: Unit tests for the attachment text extractor — text/* (and text-shaped application/*) decode
      inline; binary formats return None (the deferred-extraction seam); never raises.
Used by: pytest (tests/connectors/imap/parsing). Pure, no I/O.
Depends on: app.connectors.imap.parsing.attachment_extractor + .models.
"""

from __future__ import annotations

import pytest

from app.connectors.imap.parsing.attachment_extractor import extract_text
from app.connectors.imap.parsing.models import ParsedAttachment


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


def test_extract_text_decodes_plain_text() -> None:
    result = extract_text(_attachment("text/plain", b"hello world"))

    assert result == "hello world"


@pytest.mark.parametrize("content_type", ["application/json", "application/text", "text/csv"])
def test_extract_text_decodes_text_shaped_types(content_type: str) -> None:
    result = extract_text(_attachment(content_type, b"k=1"))

    assert result == "k=1"


def test_extract_text_returns_none_for_binary_pdf() -> None:
    # Binary extraction is deferred (CA-CONN-04) — must report absent (None), not empty/garbage.
    result = extract_text(_attachment("application/pdf", b"%PDF-1.4 binary..."))

    assert result is None


def test_extract_text_returns_none_for_empty_payload() -> None:
    assert extract_text(_attachment("text/plain", b"")) is None


def test_extract_text_bad_utf8_does_not_raise() -> None:
    result = extract_text(_attachment("text/plain", b"ok \xff\xfe bytes"))

    assert result is not None and "ok" in result
