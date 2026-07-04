"""
Role: Extract searchable TEXT from an attachment's raw bytes (design §4 lean-attachments: text is
      pulled inline, the original bytes are then discarded) and report the outcome as an
      ExtractionResult — honest NULL with a machine-readable reason, never fake text. Handles
      text/* + text-shaped application/* (charset-chain decode; html/rtf FLATTENED, never stored
      as raw markup source — audit EQ-4), PDF (extractors.pdf), docx (extractors.docx), xlsx/xlsm
      (extractors.xlsx, design §2.5) and TNEF (extractors.tnef, design §2.9); other document
      formats are honestly marked unsupported_format until their phase lands (design §5). Every
      text-bearing result then passes the SECRETS-MASKING GATE (redact_secrets, FIX_BEFORE_PROD
      EQ-2) so the stored extracted_text is secret-free by construction.
Used by: the IMAP ingest runner (step 3d) — it calls this per ParsedAttachment, stores text +
         status + detail + extractor provenance in email_attachment, then drops the bytes;
         scripts.backfill_attachment_extraction (re-runs the seam over the disk corpus).
Depends on: app.connectors.imap.parsing.models (ParsedAttachment); the connector-agnostic
            app.connectors.extraction package — .extraction_result (the contract), .pdf (PDF path),
            .docx (docx path), .xlsx (xlsx/xlsm path), .tnef (TNEF path), .text_sanitize
            (sanitize_body_text + decode_charset_chain + html_to_text — the SINGLE sanitization/
            decode/flatten sources, shared with email bodies), .redact (redact_secrets — the EQ-2
            secrets-masking gate applied to every text-bearing result); striprtf (BSD-3-Clause — RTF
            flattening, audit EQ-4); stdlib dataclasses.replace (re-stamp the masked text/detail).
Key invariants:
  - NEVER raises: a bad/undecodable attachment yields a degraded ExtractionResult, never an
    exception (per-message error isolation — one attachment must not fail the whole email).
  - HONEST NULL: text=None means "no text extracted", with `status` carrying the reason
    (extraction_status column, CHECK-pinned by migration 0015). Never '' and never garbage.
  - ONE sanitization source: every stored text (the text/* decode path included) passes through
    sanitize_body_text (CRLF→LF, C0 strip, lone-surrogate strip — storable as UTF-8, period);
    text that sanitizes to '' is stored as honest NULL with status `empty`.
  - ONE charset-decode source (audit EQ-3): text-shaped payloads decode via
    decode_charset_chain (utf-8 strict → coherence-arbitrated detection → cp1252 strict →
    windows-1251 strict → utf-8 errors='replace'), never a bare utf-8-replace decode — the audit
    measured 9 rows / 564K mojibake chars from windows-1251 files under the old utf-8-only decode;
    the detection step (audit M2) recovers undeclared cp1251 that cp1252 would mojibake.
  - MARKUP IS FLATTENED, never stored as 'extracted' source (audit EQ-4, 44 rows): text/html
    flattens through email_parser's html_to_text; text/rtf + application/rtf strip through
    striprtf — both AFTER the charset chain.
  - SECRETS-MASKING GATE (FIX_BEFORE_PROD EQ-2): every text-bearing result (extracted / truncated /
    extracted_partial_scanned) is run through redact_secrets at the seam boundary — AFTER the
    per-format extractor sanitized its text, so all formats (text/html/rtf, PDF, docx, xlsx, TNEF)
    are covered by ONE gate. extracted_text is the embeddable substrate; the 2026-06-10 audit found
    live API keys verbatim in it (from attached credential files). High-confidence credentials are
    replaced with typed `[REDACTED:...]` placeholders, and `secrets_redacted=N` is appended to
    `detail` for EQ-7 (count only — the secret is NEVER logged or embedded). A re-ingest/backfill
    cleans rows stored before the gate landed.
  - The GLOBAL size ceiling (MAX_PARSE_BYTES, design §2) applies before ANY parsing — payloads
    above it are skipped_oversize on every path (bounded memory, never-raise preserved).
  - Non-document types (images/audio/video/archives/signatures) → skipped_nondocument; document
    formats without an extractor yet (pptx/octet-stream/legacy Office incl. legacy .xls) →
    unsupported_format — the statuses later phases re-target (octet-stream sniffing; C: OCR).
"""

from __future__ import annotations

from dataclasses import replace
from functools import cache
from importlib import metadata

from striprtf.striprtf import rtf_to_text

from app.connectors.extraction.docx import extract_docx_text
from app.connectors.extraction.extraction_result import (
    STATUS_CORRUPT,
    STATUS_EMPTY,
    STATUS_EXTRACTED,
    STATUS_SKIPPED_NONDOCUMENT,
    STATUS_SKIPPED_OVERSIZE,
    STATUS_UNSUPPORTED_FORMAT,
    ExtractionResult,
)
from app.connectors.extraction.pdf import extract_pdf_text
from app.connectors.extraction.redact import redact_secrets
from app.connectors.extraction.text_sanitize import (
    decode_charset_chain,
    html_to_text,
    redecode_single_byte_text,
    sanitize_body_text,
)
from app.connectors.extraction.tnef import extract_tnef_text
from app.connectors.extraction.xlsx import extract_xlsx_text
from app.connectors.imap.parsing.models import ParsedAttachment

# Global parse ceiling (design §2): nothing above this is parsed, on ANY path — bounded memory.
MAX_PARSE_BYTES = 50 * 1024 * 1024

# Provenance for the inline text decode (no third-party engine; bump on rule changes so a
# version-aware backfill can target rows decoded under older rules).
# v2: output now flows through sanitize_body_text (CRLF→LF + C0 + surrogate strip) instead of the
# v1 bare NUL strip — the single-sanitization-source fix (2026-06-11 review).
# v3: the bare utf-8-replace decode became the STRICT charset chain shared with email bodies
# (utf-8 → cp1252 → windows-1251 → utf-8 errors='replace'; audit EQ-3 — 9 rows / 564K mojibake
# chars from windows-1251 files were stored under v2's replace-on-utf-8 decode).
TEXT_DECODER_NAME = "text-decode"
TEXT_DECODER_VERSION = "3"

# EQ-4 provenance: markup attachments record the FLATTENING engine (the transformation that
# defines the stored text), so a version-aware backfill can target rows per engine upgrade.
_HTML_FLATTENER = "html2text"
_RTF_FLATTENER = "striprtf"

# The OOXML WordprocessingML content type — dispatched to extractors.docx (design §2.4).
_DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# The OOXML SpreadsheetML content types — dispatched to extractors.xlsx (design §2.5): .xlsx plus
# macro-enabled .xlsm (same reader; macros ignored, never executed). Legacy application/vnd.ms-excel
# = .xls is an OLE binary, NOT handled here — it stays unsupported_format at the seam.
# NOTE: dispatch lowercases the incoming content_type, so EVERY constant here MUST be lowercase —
# the macro-enabled type's canonical CamelCase `macroEnabled` would otherwise never match and .xlsm
# silently fell to unsupported_format (DQ 2026-06-14).
_XLSX_CONTENT_TYPES = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel.sheet.macroenabled.12",
    }
)

# The TNEF (winmail.dat) container — dispatched to extractors.tnef (design §2.9).
_TNEF_CONTENT_TYPE = "application/ms-tnef"

# Markup types whose SOURCE must never store as 'extracted' (audit EQ-4): html flattens via the
# email-body flattener; rtf strips via striprtf. Both branch BEFORE the generic text/* prefix.
_HTML_CONTENT_TYPE = "text/html"
_RTF_CONTENT_TYPES = frozenset({"text/rtf", "application/rtf"})

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
    """Extract text from one attachment, then mask any secrets in it; never raises.

    Dispatches per content type (see _dispatch_extract), THEN runs the SECRETS-MASKING GATE
    (FIX_BEFORE_PROD EQ-2) over the result's `text`: extracted_text is the embeddable substrate
    (future Ask embeddings + retrieval), and the 2026-06-10 EQ-2 audit found LIVE API credentials
    verbatim in extracted_text (Anthropic/OpenAI/Gemini/Supabase keys, an AWS key, passwords) —
    pulled from credential files attached to emails. Every text-bearing result therefore passes
    through redact_secrets before it leaves this seam, regardless of format (text/html/rtf, PDF,
    docx, xlsx, TNEF), so the STORED column is secret-free by construction. The redaction count is
    surfaced in the result's `detail` for EQ-7 logging (count only — the secret itself is NEVER
    logged or embedded in the placeholder). The per-format extractors already sanitized their text
    (sanitize_body_text), so this gate runs strictly AFTER sanitize, as designed.

    Args:
        attachment: the parsed attachment (transient payload bytes + declared content type).

    Returns:
        An ExtractionResult; `text` is non-None only for the text-bearing statuses
        (extracted / truncated / extracted_partial_scanned), and is secret-masked when present.
    """
    return _mask_secrets(_dispatch_extract(attachment))


def _dispatch_extract(attachment: ParsedAttachment) -> ExtractionResult:
    """Route one attachment to its per-format extractor; never raises (the unmasked result).

    Dispatch: empty payload → `empty`; payload above MAX_PARSE_BYTES → `skipped_oversize`
    (nothing oversize is ever parsed); text/html → charset chain + html_to_text (EQ-4);
    text/rtf + application/rtf → charset chain + striprtf (EQ-4); other text/* + text-shaped
    application/* → charset chain + sanitize_body_text (EQ-3; `extracted`;
    sanitized-to-blank → `empty`); application/pdf → extractors.pdf (full §2.1/§3.1 pipeline);
    the docx content type → extractors.docx (§2.4); the xlsx/xlsm content types → extractors.xlsx
    (§2.5 — typed grid + text render); application/ms-tnef → extractors.tnef
    (§2.9); image/audio/video/archive/signature classes → `skipped_nondocument`; every other
    format → `unsupported_format` (later phases re-target these).
    """
    if not attachment.payload:
        return ExtractionResult(None, STATUS_EMPTY, detail="empty payload")
    if len(attachment.payload) > MAX_PARSE_BYTES:
        return ExtractionResult(
            None, STATUS_SKIPPED_OVERSIZE, detail=f"{len(attachment.payload)} bytes"
        )
    content_type = attachment.content_type.lower()
    if content_type == _HTML_CONTENT_TYPE:
        return _decode_html(attachment.payload)
    if content_type in _RTF_CONTENT_TYPES:
        return _decode_rtf(attachment.payload)
    if _is_text_like(content_type):
        return _decode_text(attachment.payload)
    if content_type == "application/pdf":
        return extract_pdf_text(attachment.payload)
    if content_type == _DOCX_CONTENT_TYPE:
        return extract_docx_text(attachment.payload)
    if content_type in _XLSX_CONTENT_TYPES:
        return extract_xlsx_text(attachment.payload)
    if content_type == _TNEF_CONTENT_TYPE:
        return extract_tnef_text(attachment.payload)
    if _is_nondocument(content_type):
        return ExtractionResult(None, STATUS_SKIPPED_NONDOCUMENT)
    return ExtractionResult(None, STATUS_UNSUPPORTED_FORMAT)


def _mask_secrets(result: ExtractionResult) -> ExtractionResult:
    """Run the EQ-2 secrets-masking gate over a result's text; honest-NULL results pass through.

    Only text-bearing results carry text to mask (extracted / truncated / extracted_partial_scanned
    — every other status has text=None). When ≥1 secret is redacted, the masked text replaces the
    original and a `secrets_redacted=N` marker is appended to `detail` for EQ-7 (count only — the
    secret is NEVER logged or embedded). When nothing is redacted the result is returned unchanged
    (cheap identity path: redact_secrets over secret-free text returns it verbatim, count 0).
    """
    if result.text is None:
        return result
    redacted, count = redact_secrets(result.text)
    if count == 0:
        return result
    marker = f"secrets_redacted={count}"
    detail = f"{result.detail} {marker}" if result.detail else marker
    return replace(result, text=redacted, detail=detail)


def _is_text_like(content_type: str) -> bool:
    return content_type.startswith(_TEXT_PREFIXES) or content_type in _TEXT_EXACT


def _is_nondocument(content_type: str) -> bool:
    return content_type.startswith(_NONDOCUMENT_PREFIXES) or content_type in _NONDOCUMENT_EXACT


def _decode_text(payload: bytes) -> ExtractionResult:
    """Decode bytes via the strict charset chain (EQ-3 — the SINGLE chain shared with email
    bodies: utf-8 → cp1252 → windows-1251 → utf-8 errors='replace'), then apply the canonical
    stored-text sanitization (sanitize_body_text — CRLF→LF, C0 strip, lone-surrogate strip);
    a text that sanitizes to blank stores as honest NULL with status `empty`."""
    try:
        text = sanitize_body_text(decode_charset_chain(payload))
    except Exception as decode_error:  # belt-and-braces: the seam must never raise
        return ExtractionResult(
            None, STATUS_CORRUPT, detail=f"text-decode:{type(decode_error).__name__}"
        )
    return _decoded_result(text, TEXT_DECODER_NAME, TEXT_DECODER_VERSION)


def _decode_html(payload: bytes) -> ExtractionResult:
    """text/html attachments: charset chain, THEN the email-body HTML flattener (EQ-4 — raw
    markup source must never store as 'extracted'), THEN the wrong-declared-codepage redecode
    (M2 — a lying charset can materialize mojibake only post-flatten); blank → honest `empty`."""
    try:
        text = sanitize_body_text(
            redecode_single_byte_text(html_to_text(decode_charset_chain(payload)))
        )
    except Exception as flatten_error:  # belt-and-braces: the seam must never raise
        return ExtractionResult(
            None, STATUS_CORRUPT, detail=f"html-flatten:{type(flatten_error).__name__}"
        )
    return _decoded_result(text, _HTML_FLATTENER, _package_version(_HTML_FLATTENER))


def _decode_rtf(payload: bytes) -> ExtractionResult:
    """text/rtf + application/rtf attachments: charset chain, THEN striprtf (EQ-4 — RTF control
    words must never store as 'extracted'; errors='replace' so a broken \\'xx escape degrades
    instead of raising), THEN the wrong-declared-codepage redecode (M2 — striprtf obeys a LYING
    \\ansicpg and mojibakes cp1251 escapes declared Latin, from a pure-ASCII source the byte
    chain cannot see); stripped-to-blank → honest `empty`."""
    try:
        text = sanitize_body_text(
            redecode_single_byte_text(
                rtf_to_text(decode_charset_chain(payload), errors="replace")
            )
        )
    except Exception as strip_error:  # belt-and-braces: the seam must never raise
        return ExtractionResult(
            None, STATUS_CORRUPT, detail=f"rtf-strip:{type(strip_error).__name__}"
        )
    return _decoded_result(text, _RTF_FLATTENER, _package_version(_RTF_FLATTENER))


def _decoded_result(text: str, extractor_name: str, extractor_version: str) -> ExtractionResult:
    """Wrap a decoded/flattened text as `extracted`, or honest-NULL `empty` when blank."""
    if not text:
        return ExtractionResult(
            None,
            STATUS_EMPTY,
            extractor_name=extractor_name,
            extractor_version=extractor_version,
        )
    return ExtractionResult(
        text,
        STATUS_EXTRACTED,
        extractor_name=extractor_name,
        extractor_version=extractor_version,
    )


@cache
def _package_version(package: str) -> str:
    """The installed package version (provenance for version-aware backfills); 'unknown' if the
    distribution metadata is unavailable (e.g. a vendored install)."""
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "unknown"
