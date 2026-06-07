"""
Role: Extract searchable TEXT from an attachment's raw bytes (design §4 lean-attachments: text is
      pulled inline, the original bytes are then discarded). v1 handles text/* only; binary formats
      (PDF / Office) are a deliberate seam returning None until the dedicated extractor slice lands.
Used by: the IMAP ingest runner (step 3d) — it calls this per ParsedAttachment, stores the returned
         text in email_attachment.extracted_text, then drops the bytes.
Depends on: app.connectors.imap.parsing.models (ParsedAttachment); stdlib only.
Key invariants:
  - NEVER raises: a bad/undecodable attachment yields None, never an exception (per-message error
    isolation — one attachment must not fail the whole email).
  - Returning None means "no text extracted (yet)", NOT "empty file". The extracted_text column is
    nullable precisely so a deferred binary format is honestly recorded as absent, not as ''.
  - BINARY EXTRACTION IS DEFERRED: see docs/FIX_BEFORE_PROD.md (CA-CONN-04) — it MUST land before a
    production path that discards attachment bytes after parse, or that text is lost permanently.
"""

from __future__ import annotations

from app.connectors.imap.parsing.models import ParsedAttachment

# Content-types whose payload is decoded as text directly. Binary formats (application/pdf,
# application/vnd.openxmlformats-*, etc.) are intentionally absent — they fall through to None.
_TEXT_PREFIXES = ("text/",)
_TEXT_EXACT = frozenset(
    {
        "application/json",
        "application/xml",
        "application/csv",
        "application/text",  # non-standard but unambiguously text (seen 83× in the real corpus)
        "message/rfc822",
    }
)


def extract_text(attachment: ParsedAttachment) -> str | None:
    """Return extracted text for an attachment, or None when the format isn't supported yet.

    text/* and a few text-shaped application/* types are decoded inline (errors='replace', best
    effort). Binary documents (PDF/Office) deliberately return None — tracked as CA-CONN-04, to be
    implemented before bytes are ever discarded in production.
    """
    if not attachment.payload:
        return None
    if _is_text_like(attachment.content_type):
        return _decode_text(attachment.payload)
    return None


def _is_text_like(content_type: str) -> bool:
    normalized = content_type.lower()
    return normalized.startswith(_TEXT_PREFIXES) or normalized in _TEXT_EXACT


def _decode_text(payload: bytes) -> str | None:
    """Decode bytes to text (utf-8, replacing undecodable runs); None if the result is blank."""
    text = payload.decode("utf-8", errors="replace").strip()
    return text or None
