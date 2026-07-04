"""
Role: Unit tests for the parser's SINGLE-PART attachment capture (2026-07-03 audit H1 + review
      fixup) and the addr-spec recipient gate (O3) — a non-multipart message whose only part is
      not served as the body becomes ONE attachment (never an empty shell, never a doubled
      body), and group-construct/malformed recipient tokens name no email_recipient row.
      Split from test_email_parser.py for the A2 size cap (that file owns the body/charset/
      robustness core; test_email_parser_dedup.py owns dedup; headers has its own module).
Used by: pytest (tests/connectors/imap/parsing). Pure — no DB, no network.
Depends on: app.connectors.imap.parsing.email_parser. Builds raw .eml bytes inline per test.
"""

from __future__ import annotations

import base64

from app.connectors.imap.parsing.email_parser import parse_email

MAILBOX = "me@oneai.com"


def _eml(headers: str, body: str) -> bytes:
    """Assemble raw RFC822 bytes from a header block + body (CRLF line endings, blank separator).

    Uses a bare-LF blank separator before the single `\\n`→`\\r\\n` pass so the header/body boundary
    becomes a CLEAN `\\r\\n\\r\\n` (not the `\\r\\r\\n` a literal CRLF yields under the replace).
    """
    return (headers.strip() + "\n\n" + body).replace("\n", "\r\n").encode("utf-8")


def test_parse_single_part_pdf_email_captured_as_attachment() -> None:
    # A bare application/pdf message (scanner / fax-to-email / ERP senders — the corpus's Kaufland
    # order mails): no multipart wrapper, so iter_attachments() yields nothing and get_body() finds
    # no text — previously an empty shell (no body, no attachment row): silent whole-document loss.
    pdf_bytes = b"%PDF-1.4 fake-but-recognizable-content"
    raw = (
        b"From: orders@kaufland.bg\r\nTo: me@oneai.com\r\nSubject: Order 7200162519\r\n"
        b"Message-ID: <pdf1@kaufland.bg>\r\n"
        b'Content-Type: application/pdf; name="order.pdf"\r\n'
        b"Content-Transfer-Encoding: base64\r\n\r\n"
    ) + base64.encodebytes(pdf_bytes)

    parsed = parse_email(raw, MAILBOX)

    assert parsed.body_text == ""  # honest: there IS no text body
    assert parsed.has_attachments is True
    assert len(parsed.attachments) == 1
    attachment = parsed.attachments[0]
    assert attachment.filename == "order.pdf"
    assert attachment.content_type == "application/pdf"
    assert attachment.payload == pdf_bytes  # decoded bytes reach the extractor seam


def test_parse_single_part_text_email_stays_body_only() -> None:
    # The H1 branch must NEVER double-store a normal single-part text email as an attachment.
    raw = _eml(
        "From: a@x.com\nTo: me@oneai.com\nSubject: T\nMessage-ID: <sp1@x>\n"
        "Content-Type: text/plain; charset=utf-8",
        "just a body",
    )

    parsed = parse_email(raw, MAILBOX)

    assert parsed.body_text == "just a body"
    assert parsed.attachments == []
    assert parsed.has_attachments is False


# — O3 (2026-07-03 audit): recipient rows require an addr-spec (the recipient mirror of L-7) —


def test_parse_group_construct_and_malformed_recipients_dropped() -> None:
    # 'undisclosed-recipients:;' (RFC group with no members) and a malformed 'mailto' token name
    # no real addressee — they must not become email_recipient rows; real addresses still do.
    raw = _eml(
        "From: a@x.com\nTo: undisclosed-recipients:;\nCc: mailto, real@acme.com\n"
        "Message-ID: <o3@x>",
        "b",
    )

    parsed = parse_email(raw, MAILBOX)

    addresses = [(r.kind, r.address) for r in parsed.recipients]
    assert ("cc", "real@acme.com") in addresses
    assert all("@" in address for _kind, address in addresses)


def test_parse_single_part_csv_email_captured_as_attachment() -> None:
    # Review fixup of the H1 branch: get_body(("plain","html")) does not serve text/csv, so a
    # single-part CSV email (exports, data deliveries) was STILL an empty shell under a
    # maintype-based guard. The guard now mirrors the body-selection call itself.
    raw = _eml(
        "From: a@x.com\nTo: me@oneai.com\nSubject: export\nMessage-ID: <csv1@x>\n"
        'Content-Type: text/csv; name="data.csv"',
        "col1;col2\n1;2",
    )

    parsed = parse_email(raw, MAILBOX)

    assert parsed.body_text == ""
    assert len(parsed.attachments) == 1
    assert parsed.attachments[0].content_type == "text/csv"
    assert parsed.attachments[0].filename == "data.csv"


def test_parse_single_part_text_marked_attachment_captured_as_attachment() -> None:
    # A single text/plain part explicitly marked Content-Disposition: attachment is not served by
    # get_body either — the sender said it is a document, so store it as one (not an empty shell).
    raw = _eml(
        "From: a@x.com\nTo: me@oneai.com\nSubject: notes\nMessage-ID: <txa1@x>\n"
        'Content-Type: text/plain\nContent-Disposition: attachment; filename="notes.txt"',
        "some notes",
    )

    parsed = parse_email(raw, MAILBOX)

    assert parsed.body_text == ""
    assert len(parsed.attachments) == 1
    assert parsed.attachments[0].filename == "notes.txt"
