"""
Role: Extract searchable TEXT from an attachment's raw bytes (design §4 lean-attachments: text is
      pulled inline, the original bytes are then discarded) and report the outcome as an
      ExtractionResult — honest NULL with a machine-readable reason, never fake text. Phase A
      handles text/* + text-shaped application/* and PDF (extractors.pdf); other document formats
      are honestly marked unsupported_format until their phase lands (design §5).
Used by: the IMAP ingest runner (step 3d) — it calls this per ParsedAttachment, stores text +
         status + extractor provenance in email_attachment, then drops the bytes;
         scripts.backfill_attachment_extraction (re-runs the seam over the disk corpus).
Depends on: app.connectors.imap.parsing.models (ParsedAttachment), .extraction_result (the
            contract), .extractors.pdf (PDF path), .email_parser (sanitize_body_text — the SINGLE
            stored-text sanitization source, shared with email bodies and PDF text).
Key invariants:
  - NEVER raises: a bad/undecodable attachment yields a degraded ExtractionResult, never an
    exception (per-message error isolation — one attachment must not fail the whole email).
  - HONEST NULL: text=None means "no text extracted", with `status` carrying the reason
    (extraction_status column, CHECK-pinned by migration 0015). Never '' and never garbage.
  - ONE sanitization source: every stored text (the text/* decode path included) passes through
    sanitize_body_text (CRLF→LF, C0 strip, lone-surrogate strip — storable as UTF-8, period);
    text that sanitizes to '' is stored as honest NULL with status `empty`.
  - The GLOBAL size ceiling (MAX_PARSE_BYTES, design §2) applies before ANY parsing — payloads
    above it are skipped_oversize on every path (bounded memory, never-raise preserved).
  - Non-document types (images/audio/video/archives/signatures) → skipped_nondocument; document
    formats without a Phase-A extractor (Office/RTF/TNEF/octet-stream) → unsupported_format —
    the statuses later phases re-target (B: TNEF + sniffing; C: OCR).
"""

from __future__ import annotations

from app.connectors.imap.parsing.email_parser import sanitize_body_text
from app.connectors.imap.parsing.extraction_result import (
    STATUS_CORRUPT,
    STATUS_EMPTY,
    STATUS_EXTRACTED,
    STATUS_SKIPPED_NONDOCUMENT,
    STATUS_SKIPPED_OVERSIZE,
    STATUS_UNSUPPORTED_FORMAT,
    ExtractionResult,
)
from app.connectors.imap.parsing.extractors.pdf import extract_pdf_text
from app.connectors.imap.parsing.models import ParsedAttachment

# Global parse ceiling (design §2): nothing above this is parsed, on ANY path — bounded memory.
MAX_PARSE_BYTES = 50 * 1024 * 1024

# Provenance for the inline text decode (no third-party engine; bump on rule changes so a
# version-aware backfill can target rows decoded under older rules).
# v2: output now flows through sanitize_body_text (CRLF→LF + C0 + surrogate strip) instead of the
# v1 bare NUL strip — the single-sanitization-source fix (2026-06-11 review).
TEXT_DECODER_NAME = "text-decode"
TEXT_DECODER_VERSION = "2"

# Content-types whose payload is decoded as text directly (unchanged from v1 in behavior).
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

# Non-document classes (design §2.11–§2.13): no text by NATURE — images/media (CON-03 owns audio
# transcription later), archives (no recursion at MVP: zip-bomb surface), S/MIME signatures,
# bounce machinery. Later phases refine these into the finer skip statuses the design proposes.
_NONDOCUMENT_PREFIXES = ("image/", "audio/", "video/")
_NONDOCUMENT_EXACT = frozenset(
    {
        "application/zip",
        "application/x-zip-compressed",
        "application/x-rar-compressed",
        "application/vnd.rar",
        "application/x-7z-compressed",
        "application/gzip",
        "application/pkcs7-signature",
        "application/x-pkcs7-signature",
        "application/pgp-signature",
        "message/delivery-status",
    }
)


def extract_text(attachment: ParsedAttachment) -> ExtractionResult:
    """Extract text from one attachment; never raises.

    Dispatch: empty payload → `empty`; payload above MAX_PARSE_BYTES → `skipped_oversize`
    (nothing oversize is ever parsed); text/* + text-shaped application/* → inline decode +
    sanitize_body_text (`extracted`; sanitized-to-blank → `empty`); application/pdf →
    extractors.pdf (full §2.1/§3.1 pipeline); image/audio/video/archive/signature classes →
    `skipped_nondocument`; every other format → `unsupported_format` (Phase B/C re-target these).

    Args:
        attachment: the parsed attachment (transient payload bytes + declared content type).

    Returns:
        An ExtractionResult; `text` is non-None only for the text-bearing statuses
        (extracted / truncated / extracted_partial_scanned).
    """
    if not attachment.payload:
        return ExtractionResult(None, STATUS_EMPTY, detail="empty payload")
    if len(attachment.payload) > MAX_PARSE_BYTES:
        return ExtractionResult(
            None, STATUS_SKIPPED_OVERSIZE, detail=f"{len(attachment.payload)} bytes"
        )
    content_type = attachment.content_type.lower()
    if _is_text_like(content_type):
        return _decode_text(attachment.payload)
    if content_type == "application/pdf":
        return extract_pdf_text(attachment.payload)
    if _is_nondocument(content_type):
        return ExtractionResult(None, STATUS_SKIPPED_NONDOCUMENT)
    return ExtractionResult(None, STATUS_UNSUPPORTED_FORMAT)


def _is_text_like(content_type: str) -> bool:
    return content_type.startswith(_TEXT_PREFIXES) or content_type in _TEXT_EXACT


def _is_nondocument(content_type: str) -> bool:
    return content_type.startswith(_NONDOCUMENT_PREFIXES) or content_type in _NONDOCUMENT_EXACT


def _decode_text(payload: bytes) -> ExtractionResult:
    """Decode bytes to text (utf-8, replacing undecodable runs), then apply the canonical
    stored-text sanitization (sanitize_body_text — CRLF→LF, C0 strip, lone-surrogate strip: the
    SINGLE source shared with email bodies and PDF text, never a re-implementation); a text that
    sanitizes to blank stores as honest NULL with status `empty`."""
    try:
        text = sanitize_body_text(payload.decode("utf-8", errors="replace"))
    except Exception as decode_error:  # belt-and-braces: the seam must never raise
        return ExtractionResult(
            None, STATUS_CORRUPT, detail=f"text-decode:{type(decode_error).__name__}"
        )
    if not text:
        return ExtractionResult(
            None,
            STATUS_EMPTY,
            extractor_name=TEXT_DECODER_NAME,
            extractor_version=TEXT_DECODER_VERSION,
        )
    return ExtractionResult(
        text,
        STATUS_EXTRACTED,
        extractor_name=TEXT_DECODER_NAME,
        extractor_version=TEXT_DECODER_VERSION,
    )
