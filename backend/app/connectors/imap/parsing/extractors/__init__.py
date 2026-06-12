"""Per-format attachment text extractors behind the attachment_extractor seam (design §2)."""

from app.connectors.imap.parsing.extractors.pdf import extract_pdf_text

__all__ = ["extract_pdf_text"]
