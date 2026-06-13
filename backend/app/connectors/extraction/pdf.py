"""
Role: PDF attachment text extraction (design §2.1) — pdfplumber primary (layout + pipe-serialized
      tables), pypdf fallback for malformed PDFs pdfminer rejects; the encrypted-PDF empty-password
      probe (§2.3); and the WHOLE-DOCUMENT scanned classification (§3.1 — Phase A: detection only,
      NO OCR). Classification is TEXT-PRESERVING: a born-digital PDF with image-only cover pages
      keeps every extracted char (`extracted_partial_scanned`); only a document whose ENTIRE text
      layer is below the char floor is marked `scanned_pending_ocr` with text=None.
Used by: the IMAP connector's attachment_extractor (dispatches application/pdf payloads);
         scripts.backfill_attachment_extraction (via the seam). The arrow points IN — this module
         imports nothing back from any connector.
Depends on: pdfplumber (MIT), pypdf (BSD) — NO PyMuPDF (AGPL, design hard-no); sibling extraction
            modules only: app.connectors.extraction.extraction_result (the result contract),
            .common (MAX_EXTRACTED_CHARS + serialize_table + package_version),
            .text_sanitize (sanitize_body_text — the SAME sanitization rules as email bodies: LF
            normalization, C0 + lone-surrogate strip; reused, never re-implemented). Imports
            NOTHING from any specific connector (connector-agnostic).
Key invariants:
  - extract_pdf_text NEVER raises: every library failure (pdfminer/pypdf raise diverse internals
    on corrupt blobs) degrades to an ExtractionResult; a final catch-all guards even our own bugs.
  - HONEST NULL, but EXTRACTED TEXT IS NEVER DISCARDED: whenever ≥ SCAN_MIN_CHARS of text exist,
    they are STORED (extracted / truncated / extracted_partial_scanned). text=None is reserved for
    encrypted/corrupt/empty and for `scanned_pending_ocr`.
  - Whole-document verdict, computed AFTER full extraction (never from a leading-page window —
    a born-digital report behind 3 image-only cover pages must keep pages 4+):
      total chars < SCAN_MIN_CHARS  → any page-covering image anywhere ⇒ `scanned_pending_ocr`
                                      (text=None — sub-floor speckle is OCR noise, not content);
                                      no covering image ⇒ extracted/rescue/empty fallthrough.
      total chars ≥ SCAN_MIN_CHARS  → ≥1 image-only page (page chars < SCAN_MIN_CHARS AND a
                                      page-covering image) ⇒ `extracted_partial_scanned`, text
                                      STORED, detail='image_only_pages=N pending OCR';
                                      else ⇒ `extracted`.
  - Phase C OCR targets BOTH `scanned_pending_ocr` AND `extracted_partial_scanned` rows. On the
    LIVE sync path those rows' bytes are NOT retained (lean attachments — the RawBlobStore gate,
    design §4): OCR-ability of live-synced rows depends on the disk corpus / a future retention
    class, and Phase C must account for that.
  - CPU/memory budget — TWO page-loop bounds (a quasi-hang PDF is not an exception: to_thread
    protects the event loop but not the worker): (1) the loop bails as soon as the materialized
    text exceeds MAX_EXTRACTED_CHARS — later pages are never parsed; (2) MAX_PDF_PAGES caps the
    pages read at all. Either bail with text found ⇒ `truncated` (truncation outranks the
    partial-scanned verdict: the unread remainder's OCR backlog is unknowable); the capped text
    IS stored (§2.1 size discipline). The char-bail `detail` reports the chars materialized
    before the bail, not the (deliberately never computed) document total.
  - `detail` carries exception CLASS NAMES / counts only — never str(exc) or document content.
    The SAME invariant binds the VENDOR loggers: pypdf interpolates raw payload bytes into its
    WARNING messages (live-reproduced: "invalid pdf header: b'SECRE'" — the first bytes of a
    misdeclared tenant attachment; 2026-06-12 Codex review), so this module mutes the pypdf and
    pdfminer loggers to CRITICAL at import (process-wide and permanent BY DESIGN: every parse of
    tenant bytes needs the suppression, and per-call level toggling would race across the
    to_thread workers). Parse diagnostics flow through extraction_status/detail instead.
  - The caller (the seam) enforces the global size ceiling BEFORE this module parses anything.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO

import pdfplumber
from pdfplumber.page import Page
from pypdf import PasswordType, PdfReader

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
    STATUS_EXTRACTED_PARTIAL_SCANNED,
    STATUS_SCANNED_PENDING_OCR,
    STATUS_TRUNCATED,
    ExtractionResult,
)
from app.connectors.extraction.text_sanitize import sanitize_body_text

logger = logging.getLogger(__name__)

# Vendor loggers interpolate RAW PAYLOAD BYTES into warnings (pypdf: "invalid pdf header:
# b'SECRE'"; pdfminer embeds stream fragments) — tenant attachment content must never reach
# logs (security.md). Muted to CRITICAL once, at import: race-free across the to_thread
# extraction workers, and the status/detail channel (class names + counts only) carries all
# the triage signal these warnings would have.
for _vendor_logger_name in ("pypdf", "pdfminer"):
    logging.getLogger(_vendor_logger_name).setLevel(logging.CRITICAL)

# CPU-budget proxy against pathological / quasi-hang PDFs: never read more pages than this.
# Text found by then → `truncated` (detail says page-bounded); none → the scanned/empty verdict
# over the pages actually seen.
MAX_PDF_PAGES = 500

# Whole-document scanned floor (§3.1) — doubles as the per-page image-only threshold: a page with
# fewer extractable chars than this AND a page-covering image counts as image-only (OCR pending).
SCAN_MIN_CHARS = 20
# An image is "page-covering" when it spans at least this fraction of the page area (§3.1: >80%).
PAGE_COVERING_AREA_RATIO = 0.8
# A zero-text document whose pages carry at least this many vector objects (curves/lines/rects)
# is CONTENT-BEARING, not empty: payroll/VAT/invoice PDFs draw their text as vector outlines, so
# no engine extracts characters and no raster image exists for the covering-image signal — the
# only honest route is the OCR queue (rasterize-then-read). 2026-06-12 quality audit EQ-1: 31 of
# 34 'empty' rows were exactly this class, silently lost on the live byte-discard path.
VECTOR_CONTENT_MIN_OBJECTS = 25
# The degraded pypdf-fallback scan signal (pdfplumber crashed → no geometry available): image
# PRESENCE on the first N pages only.
PYPDF_IMAGE_PROBE_PAGES = 3

_PDFPLUMBER = "pdfplumber"
_PYPDF = "pypdf"


@dataclass(frozen=True, slots=True)
class _PageSignal:
    """One page's classification signal from the pdfplumber pass (EVERY page read, no window)."""

    content_chars: int
    has_covering_image: bool
    has_any_image: bool
    vector_objects: int

    @property
    def is_image_only(self) -> bool:
        """An OCR-candidate page: (almost) no extractable text under a page-covering image."""
        return self.content_chars < SCAN_MIN_CHARS and self.has_covering_image


@dataclass(frozen=True, slots=True)
class _PlumberDocument:
    """The pdfplumber pass's outcome: materialized text + per-page signals + the optional bail."""

    text: str
    pages: list[_PageSignal]
    bail_reason: str | None  # non-None ⇒ the page loop stopped early (char cap / page bound)


def extract_pdf_text(payload: bytes) -> ExtractionResult:
    """Extract a PDF's text layer; never raises (every failure degrades to a status).

    Pipeline: pypdf encryption probe (empty-password attempt; still locked → `encrypted`) →
    pdfplumber per-page text + pipe-serialized tables under a '[page N]' marker (bounded by
    MAX_EXTRACTED_CHARS early-bail + MAX_PDF_PAGES) → the WHOLE-DOCUMENT scanned SIGNAL
    (sub-floor chars + a page-covering image) which first runs the pypdf text rescue —
    `scanned_pending_ocr` ONLY when BOTH engines find no text (2026-06-12 Codex review: the
    engines fail on different font pathologies; one engine's blank read must never discard a
    recoverable text layer), a rescued text layer → `extracted_partial_scanned` (text STORED,
    plumber's page signals untrustworthy) — image-only pages alongside plumber-read text →
    `extracted_partial_scanned` likewise → pypdf rescue when pdfplumber parsed but found no text
    (no scan signal) → `empty` when no engine finds text and the doc is not scan-classified.
    pdfplumber crashing entirely retries under pypdf; both failing → `corrupt` with the two
    exception class names in `detail`.

    Args:
        payload: the raw PDF bytes (the seam has already enforced the global size ceiling).

    Returns:
        An ExtractionResult; text is non-None only for the text-bearing statuses
        (extracted / truncated / extracted_partial_scanned).
    """
    try:
        return _extract(payload)
    except Exception as unexpected:  # the seam's NEVER-raise contract: even our bugs degrade
        # Class name ONLY - no exc_info: a formatted traceback ends with "Class: str(exc)",
        # and library exception strings can embed payload fragments (2026-06-12 review).
        logger.warning("pdf extraction: unexpected failure (%s)", type(unexpected).__name__)
        return ExtractionResult(
            None, STATUS_CORRUPT, detail=f"unexpected:{type(unexpected).__name__}"
        )


def _extract(payload: bytes) -> ExtractionResult:
    """The real pipeline (see extract_pdf_text); may raise — the public wrapper degrades."""
    locked = _probe_encryption(payload)
    if locked is not None:
        return locked
    try:
        document = _read_with_pdfplumber(payload)
    except Exception as plumber_error:  # pdfminer raises diverse internals on malformed PDFs
        return _pypdf_full_fallback(payload, plumber_error)
    if _looks_scanned(document.pages):
        # NEVER classify scanned on one engine's word (2026-06-12 Codex review): pdfminer and
        # pypdf fail on DIFFERENT font/CMap pathologies, so an image-backed PDF whose text layer
        # pdfplumber cannot read may still be fully readable by pypdf. Marking it scanned here
        # would store NULL and (on the live path, which discards bytes) lose the text forever.
        rescued = _pypdf_text_rescue(payload)
        if rescued is None:
            return ExtractionResult(
                None,
                STATUS_SCANNED_PENDING_OCR,
                detail="image-only pages, no text layer (both engines)",
                extractor_name=_PDFPLUMBER,
                extractor_version=_package_version(_PDFPLUMBER),
            )
        if rescued.status == STATUS_TRUNCATED:
            return rescued  # truncation outranks partial-scanned (unread remainder unknown)
        # pypdf found text the plumber missed, so the plumber's per-page char counts (and with
        # them the image-only classification) are unreliable — keep the text AND flag the row
        # for Phase C re-examination rather than claiming a clean full extraction.
        return ExtractionResult(
            rescued.text,
            STATUS_EXTRACTED_PARTIAL_SCANNED,
            detail="pypdf rescued a text layer pdfplumber missed on image-backed pages",
            extractor_name=rescued.extractor_name,
            extractor_version=rescued.extractor_version,
        )
    text = sanitize_body_text(document.text)
    if text:
        return _plumber_text_verdict(text, document)
    rescued = _pypdf_text_rescue(payload)
    if rescued is not None:
        return rescued
    if _has_undrawn_content(document.pages):
        # EQ-1 (2026-06-12 quality audit): zero extractable text but the pages CARRY content —
        # vector-drawn documents (payroll/invoice text as curves) or inset scans without a
        # page-covering image. 'empty' would route them out of every recovery path; the OCR
        # queue (rasterize-then-read) is the only honest destination.
        return ExtractionResult(
            None,
            STATUS_SCANNED_PENDING_OCR,
            detail="no text layer; vector/image content present (rasterize for OCR)",
            extractor_name=_PDFPLUMBER,
            extractor_version=_package_version(_PDFPLUMBER),
        )
    return ExtractionResult(
        None,
        STATUS_EMPTY,
        extractor_name=_PDFPLUMBER,
        extractor_version=_package_version(_PDFPLUMBER),
    )


def _looks_scanned(pages: list[_PageSignal]) -> bool:
    """The WHOLE-DOCUMENT §3.1 mark-for-OCR signal: total extractable chars below SCAN_MIN_CHARS
    with at least one page-covering image anywhere. A document clearing the char floor is NEVER
    marked scanned — its text is stored (extracted_partial_scanned when image-only pages remain).
    This is a SIGNAL, not the verdict: the caller still runs the pypdf rescue first, and only a
    document NO engine can read is finally classified scanned_pending_ocr."""
    if not pages:
        return False
    total_chars = sum(page.content_chars for page in pages)
    return total_chars < SCAN_MIN_CHARS and any(page.has_covering_image for page in pages)


def _has_undrawn_content(pages: list[_PageSignal]) -> bool:
    """True when a ZERO-TEXT document still carries drawable content worth OCR (EQ-1).

    Either any image at all (an inset scan needs no page-covering geometry to hold the actual
    document) or a document-total vector-object count at/over VECTOR_CONTENT_MIN_OBJECTS
    (text drawn as curves/lines/rects — the payroll/invoice class). A genuinely blank PDF has
    neither and stays `empty`.
    """
    if any(page.has_any_image for page in pages):
        return True
    return sum(page.vector_objects for page in pages) >= VECTOR_CONTENT_MIN_OBJECTS


def _vector_object_count(page: Page) -> int:
    """Best-effort count of a page's vector objects (curves + lines + rects); 0 on failure."""
    try:
        return len(page.curves) + len(page.lines) + len(page.rects)
    except Exception:
        return 0


def _plumber_text_verdict(text: str, document: _PlumberDocument) -> ExtractionResult:
    """extracted / truncated / extracted_partial_scanned for a successful pdfplumber pass.

    Truncation (an early bail, or text still over the cap) outranks partial-scanned: a bailed
    read cannot know the unread remainder's OCR backlog. Otherwise ≥1 image-only page (sub-floor
    chars under a page-covering image) marks the row for Phase C OCR WITHOUT discarding the text
    already extracted from the born-digital pages.
    """
    result = _text_result(text, _PDFPLUMBER, document.bail_reason)
    if result.status != STATUS_EXTRACTED:
        return result
    image_only_pages = sum(1 for page in document.pages if page.is_image_only)
    if image_only_pages == 0:
        return result
    return ExtractionResult(
        result.text,
        STATUS_EXTRACTED_PARTIAL_SCANNED,
        detail=f"image_only_pages={image_only_pages} pending OCR",
        extractor_name=_PDFPLUMBER,
        extractor_version=_package_version(_PDFPLUMBER),
    )


def _probe_encryption(payload: bytes) -> ExtractionResult | None:
    """Return an `encrypted` result when the PDF stays locked after the empty-password attempt.

    Owner-password-only locks (empty user password) decrypt transparently — the mailbox owner
    legitimately received the file (design §2.3; no password vault at MVP). Probe failures return
    None: a PDF pypdf cannot even open is judged by the pdfplumber→pypdf extraction chain, not
    prematurely here.
    """
    try:
        reader = PdfReader(BytesIO(payload))
        if not reader.is_encrypted:
            return None
        if reader.decrypt("") != PasswordType.NOT_DECRYPTED:
            return None  # opened with the empty password — extraction may proceed
    except Exception:
        return None
    return ExtractionResult(
        None,
        STATUS_ENCRYPTED,
        detail="user password required",
        extractor_name=_PYPDF,
        extractor_version=_package_version(_PYPDF),
    )


def _read_with_pdfplumber(payload: bytes) -> _PlumberDocument:
    """Primary pass: per-page '[page N]' marker + text + serialized tables + per-page signals.

    Reads EVERY page (the whole-document verdict needs them all) within the two CPU bounds: the
    loop bails as soon as the materialized text exceeds MAX_EXTRACTED_CHARS (later pages are never
    parsed — no further extract_text/extract_tables) or once MAX_PDF_PAGES pages have been read;
    `bail_reason` records why and the verdict turns it into `truncated`. Returns text='' when NO
    page yielded any text or table content — the page markers alone must never masquerade as
    extracted text.
    """
    chunks: list[str] = []
    pages: list[_PageSignal] = []
    content_chars = 0
    materialized_chars = 0  # length of '\n\n'.join(chunks) so far — the early-bail counter
    bail_reason: str | None = None
    with pdfplumber.open(BytesIO(payload), password="") as document:
        for number, page in enumerate(document.pages, start=1):
            if number > MAX_PDF_PAGES:
                bail_reason = f"page-bounded: stopped after {MAX_PDF_PAGES} pages"
                break
            page_text = page.extract_text() or ""
            tables = _serialized_tables(page)
            page_content_chars = len(page_text.strip()) + len(tables)
            content_chars += page_content_chars
            pages.append(
                _PageSignal(
                    page_content_chars,
                    _has_page_covering_image(page),
                    has_any_image=bool(page.images),
                    vector_objects=_vector_object_count(page),
                )
            )
            chunk = f"[page {number}]\n{page_text}"
            if tables:
                chunk = f"{chunk}\n{tables}"
            materialized_chars += len(chunk) + (2 if chunks else 0)  # + the '\n\n' join seam
            chunks.append(chunk)
            if materialized_chars > MAX_EXTRACTED_CHARS:
                # The count is the chars MATERIALIZED before the bail — the full-document total
                # is deliberately never computed (that materialization is what the bail prevents).
                bail_reason = f"capped from {materialized_chars} chars"
                break
    return _PlumberDocument("\n\n".join(chunks) if content_chars else "", pages, bail_reason)


def _serialized_tables(page: Page) -> str:
    """All of a page's tables, pipe-serialized; '' when none. Table detection is best-effort
    garnish — its failure must not lose the page's text, so errors degrade to ''."""
    try:
        tables = page.extract_tables()
    except Exception:
        return ""
    rendered = [serialize_table(table) for table in tables]
    return "\n".join(text for text in rendered if text)


def _has_page_covering_image(page: Page) -> bool:
    """True when any image on the page spans ≥ PAGE_COVERING_AREA_RATIO of the page area (§3.1)."""
    page_area = float(page.width) * float(page.height)
    if page_area <= 0:
        return False
    for image in page.images:
        width = float(image.get("x1", 0)) - float(image.get("x0", 0))
        height = float(image.get("bottom", 0)) - float(image.get("top", 0))
        if width * height >= PAGE_COVERING_AREA_RATIO * page_area:
            return True
    return False


def _pypdf_full_fallback(payload: bytes, plumber_error: Exception) -> ExtractionResult:
    """pdfplumber crashed — retry the whole extraction under pypdf (§2.1 failure handling).

    pypdf also failing → `corrupt` (both class names in detail). pypdf parsing but finding no
    text → scanned-vs-empty by image PRESENCE on the first PYPDF_IMAGE_PROBE_PAGES pages: the
    page-covering geometry test needs pdfplumber, which just failed, so presence is the best
    remaining scan signal (this degraded path keeps a probe window and never produces the
    partial-scanned verdict — no per-page geometry to base it on).
    """
    try:
        reader = _open_pypdf(payload)
        document_text, bail_reason = _pypdf_document_text(reader)
    except Exception as pypdf_error:
        return ExtractionResult(
            None,
            STATUS_CORRUPT,
            detail=(
                f"pdfplumber:{type(plumber_error).__name__} pypdf:{type(pypdf_error).__name__}"
            ),
        )
    text = sanitize_body_text(document_text)
    if text:
        return _text_result(text, _PYPDF, bail_reason)
    status = STATUS_SCANNED_PENDING_OCR if _pypdf_probe_has_images(reader) else STATUS_EMPTY
    return ExtractionResult(
        None,
        status,
        detail=f"pdfplumber:{type(plumber_error).__name__} (pypdf parsed, no text)",
        extractor_name=_PYPDF,
        extractor_version=_package_version(_PYPDF),
    )


def _pypdf_text_rescue(payload: bytes) -> ExtractionResult | None:
    """pdfplumber parsed but extracted nothing — see whether pypdf finds a text layer (§2.1).

    Returns the wrapped extracted/truncated result, or None when pypdf finds no text either. A
    crash here never escapes: the primary parse already succeeded, so a fallback failure must not
    flip the verdict to corrupt.
    """
    try:
        document_text, bail_reason = _pypdf_document_text(_open_pypdf(payload))
    except Exception:
        return None
    text = sanitize_body_text(document_text)
    if not text:
        return None
    return _text_result(text, _PYPDF, bail_reason)


def _open_pypdf(payload: bytes) -> PdfReader:
    """A PdfReader over the payload; empty-password-locked docs are opened transparently
    (the encryption probe already proved the empty password works before extraction began)."""
    reader = PdfReader(BytesIO(payload))
    if reader.is_encrypted:
        reader.decrypt("")
    return reader


def _pypdf_document_text(reader: PdfReader) -> tuple[str, str | None]:
    """Whole-document text under pypdf: the same '[page N]' markers and the same two CPU bounds
    (char-cap early-bail + MAX_PDF_PAGES) as the primary pass. Returns (text, bail_reason);
    text='' when no page yields any (markers alone must never masquerade as content)."""
    chunks: list[str] = []
    content_chars = 0
    materialized_chars = 0
    bail_reason: str | None = None
    for number, page in enumerate(reader.pages, start=1):
        if number > MAX_PDF_PAGES:
            bail_reason = f"page-bounded: stopped after {MAX_PDF_PAGES} pages"
            break
        page_text = page.extract_text() or ""
        content_chars += len(page_text.strip())
        chunk = f"[page {number}]\n{page_text}"
        materialized_chars += len(chunk) + (2 if chunks else 0)
        chunks.append(chunk)
        if materialized_chars > MAX_EXTRACTED_CHARS:
            bail_reason = f"capped from {materialized_chars} chars"
            break
    return ("\n\n".join(chunks) if content_chars else "", bail_reason)


def _pypdf_probe_has_images(reader: PdfReader) -> bool:
    """Image PRESENCE on the probe pages (the degraded scan signal — no geometry available)."""
    try:
        return any(len(page.images) > 0 for page in reader.pages[:PYPDF_IMAGE_PROBE_PAGES])
    except Exception:
        return False


def _text_result(text: str, extractor: str, bail_reason: str | None) -> ExtractionResult:
    """Wrap sanitized text as `extracted` — or as `truncated` when the page loop bailed early
    (char cap / page bound) or the text still exceeds MAX_EXTRACTED_CHARS. The capped text IS
    stored; `detail` carries the bail reason (or the post-hoc cap count)."""
    version = _package_version(extractor)
    if bail_reason is None and len(text) <= MAX_EXTRACTED_CHARS:
        return ExtractionResult(
            text, STATUS_EXTRACTED, extractor_name=extractor, extractor_version=version
        )
    return ExtractionResult(
        text[:MAX_EXTRACTED_CHARS],
        STATUS_TRUNCATED,
        detail=bail_reason or f"capped from {len(text)} chars",
        extractor_name=extractor,
        extractor_version=version,
    )


