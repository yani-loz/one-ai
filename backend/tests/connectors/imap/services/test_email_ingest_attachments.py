"""
Role: End-to-end per-format attachment-extraction tests for EmailIngestService — one raw email with
      a binary attachment → an email_attachment row carrying the ExtractionResult's text + status +
      detail (EQ-7, 0016) + extractor provenance (0015) + the typed structured grid (0017, xlsx).
      Split from test_email_ingest_service.py (A2 size ceiling): that file owns the message/entity/
      idempotency/isolation core; this file owns the binary-format extraction matrix (pdf/docx/xlsx/
      tnef/image + the corrupt + non-xlsx-NULL cases).
Used by: pytest (tests/connectors/imap/services). Real DB via the services conftest.
Depends on: app.connectors.imap.services.email_ingest_service + the email models; the in-memory
            extractor fixture builders (conftest.build_pdf/build_docx/build_xlsx/build_tnef).
"""

from __future__ import annotations

from base64 import b64encode
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.imap.models.email import EmailAttachment
from app.connectors.imap.services.email_ingest_service import EmailIngestService
from tests.connectors.extraction.conftest import (
    TEXT_PAGE_STREAM,
    build_docx,
    build_pdf,
    build_tnef,
    build_xlsx,
)
from tests.connectors.imap.services.conftest import seed_connection


async def _only_attachment(db_session: AsyncSession, org_id: object) -> EmailAttachment:
    """The single attachment row of `org_id` (every test here ingests exactly one)."""
    return (
        await db_session.execute(
            select(EmailAttachment).where(EmailAttachment.org_id == org_id)
        )
    ).scalar_one()


async def test_ingest_corrupt_pdf_attachment_records_null_text_with_corrupt_status(
    db_session: AsyncSession,
) -> None:
    # CA-CONN-04 Phase A: an unparseable PDF stores with extracted_text NULL — and the row now
    # SAYS why (extraction_status='corrupt'), never silently absent (0015).
    org = uuid4()
    connection = await seed_connection(db_session, org)
    service = EmailIngestService(db_session, connection)
    raw = (
        b"From: a@globex.com\r\nTo: owner@acme.com\r\nMessage-ID: <bin@x>\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
        b"--B\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
        b'--B\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename="d.pdf"'
        b"\r\n\r\n%PDF-1.4 binary\r\n--B--\r\n"
    )

    await service.ingest_email(raw)

    attachment = await _only_attachment(db_session, org)
    assert attachment.content_type == "application/pdf"
    assert attachment.size_bytes > 0
    assert attachment.extracted_text is None  # honest absent, not empty-string
    assert attachment.extraction_status == "corrupt"
    # EQ-7 (0016): the WHY survives to the row — exception class names, never payload content.
    assert attachment.extraction_detail is not None
    assert "%PDF" not in attachment.extraction_detail


async def test_ingest_valid_pdf_attachment_stores_text_and_extraction_provenance(
    db_session: AsyncSession,
) -> None:
    # End-to-end Phase A: a real (minimal, hand-crafted) PDF lands with its text layer extracted
    # and the 0015 status + provenance columns filled from the ExtractionResult.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    service = EmailIngestService(db_session, connection)
    pdf_b64 = b64encode(build_pdf([TEXT_PAGE_STREAM]))
    raw = (
        b"From: a@globex.com\r\nTo: owner@acme.com\r\nMessage-ID: <pdf@x>\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
        b"--B\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
        b"--B\r\nContent-Type: application/pdf\r\n"
        b'Content-Disposition: attachment; filename="contract.pdf"\r\n'
        b"Content-Transfer-Encoding: base64\r\n\r\n" + pdf_b64 + b"\r\n--B--\r\n"
    )

    await service.ingest_email(raw)

    attachment = await _only_attachment(db_session, org)
    assert attachment.extracted_text == "[page 1]\nHello World"
    assert attachment.extraction_status == "extracted"
    assert attachment.extractor_name == "pdfplumber"
    assert attachment.extractor_version is not None


async def test_ingest_valid_docx_attachment_stores_text_and_extraction_provenance(
    db_session: AsyncSession,
) -> None:
    # End-to-end docx slice (design §2.4): a real python-docx-built attachment lands with its
    # body text extracted and the 0015 status + provenance columns filled.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    service = EmailIngestService(db_session, connection)
    docx_b64 = b64encode(build_docx(["Quarterly summary attached."]))
    docx_type = b"application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    raw = (
        b"From: a@globex.com\r\nTo: owner@acme.com\r\nMessage-ID: <docx@x>\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
        b"--B\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
        b"--B\r\nContent-Type: " + docx_type + b"\r\n"
        b'Content-Disposition: attachment; filename="summary.docx"\r\n'
        b"Content-Transfer-Encoding: base64\r\n\r\n" + docx_b64 + b"\r\n--B--\r\n"
    )

    await service.ingest_email(raw)

    attachment = await _only_attachment(db_session, org)
    assert attachment.filename == "summary.docx"
    assert attachment.extracted_text == "Quarterly summary attached."
    assert attachment.extraction_status == "extracted"
    assert attachment.extractor_name == "python-docx"
    assert attachment.extractor_version is not None


async def test_ingest_valid_xlsx_attachment_stores_text_grid_and_provenance(
    db_session: AsyncSession,
) -> None:
    # End-to-end xlsx slice (design §2.5): a real openpyxl-built workbook lands with its text
    # render extracted, the 0015 provenance columns filled, AND the typed cell grid persisted to
    # the 0017 extracted_data JSONB — a known cell value reads back from the structured grid.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    service = EmailIngestService(db_session, connection)
    xlsx_b64 = b64encode(build_xlsx(sheets=[("Budget", [["Item", "Cost"], ["Servers", 4200]])]))
    xlsx_type = b"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    raw = (
        b"From: a@globex.com\r\nTo: owner@acme.com\r\nMessage-ID: <xlsx@x>\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
        b"--B\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
        b"--B\r\nContent-Type: " + xlsx_type + b"\r\n"
        b'Content-Disposition: attachment; filename="budget.xlsx"\r\n'
        b"Content-Transfer-Encoding: base64\r\n\r\n" + xlsx_b64 + b"\r\n--B--\r\n"
    )

    await service.ingest_email(raw)

    attachment = await _only_attachment(db_session, org)
    assert attachment.filename == "budget.xlsx"
    assert attachment.extraction_status == "extracted"
    assert attachment.extracted_text is not None
    assert "[sheet: Budget]" in attachment.extracted_text
    assert "Servers | 4200" in attachment.extracted_text
    assert attachment.extractor_name == "openpyxl"
    assert attachment.extractor_version is not None
    # 0017: the typed grid round-trips through JSONB — a known cell reads back with its type+value.
    assert attachment.extracted_data is not None
    assert attachment.extracted_data["format"] == "xlsx-grid-v1"
    cells = {c["ref"]: c for c in attachment.extracted_data["sheets"][0]["cells"]}
    assert cells["B2"] == {"ref": "B2", "t": "n", "v": 4200}
    assert attachment.extraction_detail == "sheets=1 cells=4"


async def test_ingest_non_xlsx_attachment_leaves_extracted_data_null(
    db_session: AsyncSession,
) -> None:
    # Backward-compat: a non-xlsx extractor leaves structured None → the 0017 column stays NULL.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    service = EmailIngestService(db_session, connection)
    raw = (
        b"From: a@globex.com\r\nTo: owner@acme.com\r\nMessage-ID: <csvnull@x>\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
        b"--B\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
        b"--B\r\nContent-Type: text/csv\r\n"
        b'Content-Disposition: attachment; filename="d.csv"\r\n\r\na,b\r\n1,2\r\n--B--\r\n'
    )

    await service.ingest_email(raw)

    attachment = await _only_attachment(db_session, org)
    assert attachment.extraction_status == "extracted"
    assert attachment.extracted_data is None  # honest None on the ORM round-trip
    # ...AND honest SQL NULL in storage, not the JSONB literal 'null': the ORM reads a stored
    # 'null'::jsonb BACK as Python None, masking the difference — only raw SQL catches it. Without
    # none_as_null=True every prose row stores 'null' and `WHERE extracted_data IS NOT NULL`
    # matches everything (caught live on the corpus: 10,030 json-null rows).
    storage_type = await db_session.execute(
        text(
            "SELECT jsonb_typeof(extracted_data), extracted_data IS NULL "
            "FROM email_attachment WHERE id = :id"
        ),
        {"id": attachment.id},
    )
    json_type, is_sql_null = storage_type.one()
    assert is_sql_null is True
    assert json_type is None  # NOT 'null' — that would be a present JSONB value


async def test_ingest_tnef_attachment_stores_text_with_detail_persisted(
    db_session: AsyncSession,
) -> None:
    # End-to-end TNEF slice (design §2.9 + EQ-7): a winmail.dat attachment lands with its body
    # + embedded-file text extracted, tnefparse provenance, AND ExtractionResult.detail
    # persisted to the 0016 extraction_detail column (both write seams previously dropped it).
    org = uuid4()
    connection = await seed_connection(db_session, org)
    service = EmailIngestService(db_session, connection)
    tnef_payload = build_tnef(
        rtf_body=b"{\\rtf1\\ansi The real Outlook body.}",
        attachments=[(b"notes.txt", b"embedded meeting notes")],
    )
    tnef_b64 = b64encode(tnef_payload)
    raw = (
        b"From: a@globex.com\r\nTo: owner@acme.com\r\nMessage-ID: <tnef@x>\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
        b"--B\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
        b"--B\r\nContent-Type: application/ms-tnef\r\n"
        b'Content-Disposition: attachment; filename="winmail.dat"\r\n'
        b"Content-Transfer-Encoding: base64\r\n\r\n" + tnef_b64 + b"\r\n--B--\r\n"
    )

    await service.ingest_email(raw)

    attachment = await _only_attachment(db_session, org)
    assert attachment.filename == "winmail.dat"
    assert attachment.extraction_status == "extracted"
    assert attachment.extracted_text is not None
    assert "The real Outlook body." in attachment.extracted_text
    assert "[embedded: notes.txt]\nembedded meeting notes" in attachment.extracted_text
    assert attachment.extractor_name == "tnefparse"
    assert attachment.extractor_version is not None
    assert attachment.extraction_detail == "body=rtf embedded_files=1"  # EQ-7: detail persisted


async def test_ingest_image_attachment_records_skipped_nondocument_status(
    db_session: AsyncSession,
) -> None:
    # Design §2.11: images are correctly skipped — NULL text WITH the machine-readable reason.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    service = EmailIngestService(db_session, connection)
    raw = (
        b"From: a@globex.com\r\nTo: owner@acme.com\r\nMessage-ID: <img@x>\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
        b"--B\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
        b'--B\r\nContent-Type: image/png\r\nContent-Disposition: inline; filename="logo.png"'
        b"\r\n\r\n\x89PNG fake pixels\r\n--B--\r\n"
    )

    await service.ingest_email(raw)

    attachment = await _only_attachment(db_session, org)
    assert attachment.extracted_text is None
    assert attachment.extraction_status == "skipped_nondocument"
    assert attachment.extractor_name is None  # no engine ran — nothing to attribute
