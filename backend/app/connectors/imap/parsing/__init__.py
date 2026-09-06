"""IMAP email parsing — raw RFC822 bytes → structured ParsedEmail + inline attachment text."""

from app.connectors.extraction.extraction_result import ExtractionResult
from app.connectors.imap.parsing.attachment_extractor import extract_text
from app.connectors.imap.parsing.email_parser import DISCLOSED_RECIPIENT_KINDS, parse_email
from app.connectors.imap.parsing.models import ParsedAttachment, ParsedEmail, ParsedRecipient

__all__ = [
    "DISCLOSED_RECIPIENT_KINDS",
    "ExtractionResult",
    "ParsedAttachment",
    "ParsedEmail",
    "ParsedRecipient",
    "extract_text",
    "parse_email",
]
