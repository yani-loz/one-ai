"""IMAP email parsing — raw RFC822 bytes → structured ParsedEmail + inline attachment text."""

from app.connectors.imap.parsing.attachment_extractor import extract_text
from app.connectors.imap.parsing.email_parser import parse_email
from app.connectors.imap.parsing.models import ParsedAttachment, ParsedEmail, ParsedRecipient

__all__ = [
    "ParsedAttachment",
    "ParsedEmail",
    "ParsedRecipient",
    "extract_text",
    "parse_email",
]
