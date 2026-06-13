"""
Role: The attachment-extraction contract — ExtractionResult (what every extractor returns) and the
      closed extraction-status vocabulary stored in email_attachment.extraction_status. Honest NULL
      becomes honest-NULL-WITH-A-REASON: every attachment row carries a machine-readable status so
      future backfills can target exactly the rows a better extractor would improve (design §2).
Used by: app.connectors.extraction.pdf / .docx / .tnef (produce them); the IMAP connector's
         attachment_extractor (the seam returns these), its email model (the status vocabulary the
         0015 CHECK pins) and email_ingest_service (stores status + provenance columns);
         scripts.backfill_attachment_extraction; migration 0015 + tests/db (the DB CHECK pins this
         exact vocabulary). The arrow points IN — this leaf imports nothing back.
Depends on: stdlib only (dataclasses). NOTHING from any specific connector (connector-agnostic).
Key invariants:
  - EXTRACTION_STATUSES is the single Python source of truth; the DB CHECK in migration
    0015_attachment_extraction_status pins the SAME literal set — keep them in lockstep
    (tests/db/test_attachment_extraction_status.py asserts the two never drift).
  - `text` is non-None ONLY for the TEXT_BEARING_STATUSES (extracted / truncated /
    extracted_partial_scanned); every other status means "no text stored" with `status` carrying
    the reason (honest NULL, never fake/empty text).
  - STATUS_PENDING is the DB default for not-yet-attempted rows (and the backfill's work queue);
    a live extractor run never RETURNS it.
  - Phase C OCR targets BOTH scanned statuses: STATUS_SCANNED_PENDING_OCR (no usable text layer,
    text=None) and STATUS_EXTRACTED_PARTIAL_SCANNED (born-digital text STORED, image-only pages
    still pending OCR).
  - `detail` must never embed attachment content verbatim (it lands in the DB/logs): exception
    CLASS names and counts only.
"""

from __future__ import annotations

from dataclasses import dataclass

# ── Status vocabulary (design §2, Phase A) — mirrored by the 0015 DB CHECK; keep in sync. ──
STATUS_PENDING = "pending"  # not yet attempted (DB default; the backfill's work queue)
STATUS_EXTRACTED = "extracted"  # text stored, complete
STATUS_TRUNCATED = "truncated"  # text stored, capped at MAX_EXTRACTED_CHARS
STATUS_EMPTY = "empty"  # parsed fine, genuinely no text content
STATUS_CORRUPT = "corrupt"  # unparseable bytes (detail = exception class names)
STATUS_ENCRYPTED = "encrypted"  # password-protected beyond the empty-password probe
STATUS_SCANNED_PENDING_OCR = "scanned_pending_ocr"  # image-only PDF: marked for Phase C OCR
# Born-digital text STORED, but ≥1 image-only page remains (scan/cover pages): Phase C OCR queue
# alongside scanned_pending_ocr — the extracted text is never discarded.
STATUS_EXTRACTED_PARTIAL_SCANNED = "extracted_partial_scanned"
STATUS_UNSUPPORTED_FORMAT = "unsupported_format"  # document format with no extractor yet
STATUS_SKIPPED_OVERSIZE = "skipped_oversize"  # payload above the parse ceiling (bounded memory)
STATUS_SKIPPED_NONDOCUMENT = "skipped_nondocument"  # images/archives/media — no text by nature

EXTRACTION_STATUSES: frozenset[str] = frozenset(
    {
        STATUS_PENDING,
        STATUS_EXTRACTED,
        STATUS_TRUNCATED,
        STATUS_EMPTY,
        STATUS_CORRUPT,
        STATUS_ENCRYPTED,
        STATUS_SCANNED_PENDING_OCR,
        STATUS_EXTRACTED_PARTIAL_SCANNED,
        STATUS_UNSUPPORTED_FORMAT,
        STATUS_SKIPPED_OVERSIZE,
        STATUS_SKIPPED_NONDOCUMENT,
    }
)

# Statuses that carry stored text — every other status implies text is None (honest NULL).
TEXT_BEARING_STATUSES: frozenset[str] = frozenset(
    {STATUS_EXTRACTED, STATUS_TRUNCATED, STATUS_EXTRACTED_PARTIAL_SCANNED}
)


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """One extraction attempt's outcome: the text (or honest None) + the reason + provenance.

    `extractor_name`/`extractor_version` identify WHICH engine produced this outcome (e.g.
    'pdfplumber' 0.11.9) so a version-aware backfill can re-run exactly the rows an upgraded
    extractor would improve; None when no engine ran (skips / unsupported formats).
    """

    text: str | None
    status: str
    detail: str | None = None
    extractor_name: str | None = None
    extractor_version: str | None = None
