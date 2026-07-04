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
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.imap.models.email import EmailAttachment, EmailMessage
from app.connectors.imap.services import email_ingest_service as email_ingest_service_module
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


def _pdf_email(message_id: bytes, pdf_b64: bytes) -> bytes:
    """A multipart email carrying the given base64 PDF (distinct Message-ID per call)."""
    return (
        b"From: a@globex.com\r\nTo: owner@acme.com\r\nMessage-ID: <" + message_id + b">\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
        b"--B\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
        b"--B\r\nContent-Type: application/pdf\r\n"
        b'Content-Disposition: attachment; filename="contract.pdf"\r\n'
        b"Content-Transfer-Encoding: base64\r\n\r\n" + pdf_b64 + b"\r\n--B--\r\n"
    )


async def test_ingest_duplicate_attachment_reuses_prior_extraction_without_rerunning(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Content-addressed extraction (perf lane #1): the SAME bytes in a second email must copy the
    # first row's outcome — text, status, AND engine provenance — without invoking any extractor.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    service = EmailIngestService(db_session, connection)
    pdf_b64 = b64encode(build_pdf([TEXT_PAGE_STREAM]))
    await service.ingest_email(_pdf_email(b"dup-one@x", pdf_b64))

    def _explode(_: object) -> None:
        raise AssertionError("extractor ran for a byte-identical duplicate")

    monkeypatch.setattr(email_ingest_service_module, "extract_text", _explode)
    await service.ingest_email(_pdf_email(b"dup-two@x", pdf_b64))

    rows = (
        (
            await db_session.execute(
                select(EmailAttachment)
                .where(EmailAttachment.org_id == org)
                .order_by(EmailAttachment.created_at, EmailAttachment.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    original, reused = rows
    assert reused.extracted_text == original.extracted_text == "[page 1]\nHello World"
    assert reused.extraction_status == "extracted"
    assert reused.extractor_name == original.extractor_name == "pdfplumber"
    assert reused.extractor_version == original.extractor_version


async def test_ingest_duplicate_xlsx_reuses_structured_grid_intact(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The reuse copy must round-trip the 0017 JSONB grid too (2026-07-04 review L15): a codec
    # regression (e.g. double-encoding on the copy) would corrupt the structured layer silently
    # if only text-bearing formats were asserted.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    service = EmailIngestService(db_session, connection)
    xlsx_b64 = b64encode(build_xlsx(sheets=[("Grid", [["Item", "Cost"], ["Servers", 4200]])]))
    xlsx_type = b"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    def _xlsx_email(message_id: bytes) -> bytes:
        return (
            b"From: a@globex.com\r\nTo: owner@acme.com\r\nMessage-ID: <" + message_id + b">\r\n"
            b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
            b"--B\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
            b"--B\r\nContent-Type: " + xlsx_type + b"\r\n"
            b'Content-Disposition: attachment; filename="grid.xlsx"\r\n'
            b"Content-Transfer-Encoding: base64\r\n\r\n" + xlsx_b64 + b"\r\n--B--\r\n"
        )

    await service.ingest_email(_xlsx_email(b"xlsx-dup-one@x"))

    def _explode(_: object) -> None:
        raise AssertionError("extractor ran for a byte-identical duplicate")

    monkeypatch.setattr(email_ingest_service_module, "extract_text", _explode)
    await service.ingest_email(_xlsx_email(b"xlsx-dup-two@x"))

    rows = (
        (
            await db_session.execute(
                select(EmailAttachment)
                .where(EmailAttachment.org_id == org)
                .order_by(EmailAttachment.created_at, EmailAttachment.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    original, reused = rows
    assert reused.extracted_data == original.extracted_data
    assert reused.extracted_data["format"] == "xlsx-grid-v1"
    cells = {c["ref"]: c for c in reused.extracted_data["sheets"][0]["cells"]}
    assert cells["B2"] == {"ref": "B2", "t": "n", "v": 4200}  # typed cell survives the copy


async def test_ingest_same_bytes_under_different_content_type_runs_own_extraction(
    db_session: AsyncSession,
) -> None:
    # The reuse key is (org, content_hash, content_type): identical bytes declared as a different
    # type dispatch to a different extractor, so the second attachment must NOT copy the first.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    service = EmailIngestService(db_session, connection)
    body = b"plain words"
    raw_text = (
        b"From: a@globex.com\r\nTo: owner@acme.com\r\nMessage-ID: <ct-one@x>\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
        b"--B\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
        b"--B\r\nContent-Type: text/plain\r\n"
        b'Content-Disposition: attachment; filename="d.txt"\r\n\r\n' + body + b"\r\n--B--\r\n"
    )
    raw_binary = (
        b"From: a@globex.com\r\nTo: owner@acme.com\r\nMessage-ID: <ct-two@x>\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
        b"--B\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
        b"--B\r\nContent-Type: application/octet-stream\r\n"
        b'Content-Disposition: attachment; filename="d.bin"\r\n\r\n' + body + b"\r\n--B--\r\n"
    )

    await service.ingest_email(raw_text)
    await service.ingest_email(raw_binary)

    statuses = {
        row.content_type: row.extraction_status
        for row in (
            await db_session.execute(
                select(EmailAttachment).where(
                    EmailAttachment.org_id == org,
                    EmailAttachment.filename.in_(["d.txt", "d.bin"]),
                )
            )
        )
        .scalars()
        .all()
    }
    assert statuses["text/plain"] == "extracted"
    assert statuses["application/octet-stream"] == "unsupported_format"


async def test_ingest_with_pending_prior_row_runs_extraction_instead_of_reusing(
    db_session: AsyncSession,
) -> None:
    # 'pending' means extraction never RAN (DB-default rows predating an extractor) — there is no
    # outcome to copy, so a matching-hash pending row must not suppress a real extraction.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    payload = b"pending twin payload"
    seeded_message = EmailMessage(
        org_id=org,
        connection_id=connection.id,
        dedup_key="seed-pending-twin",
        headers={},
    )
    db_session.add(seeded_message)
    await db_session.flush()
    db_session.add(
        EmailAttachment(
            org_id=org,
            email_id=seeded_message.id,
            filename="old.txt",
            content_type="text/plain",
            size_bytes=len(payload),
            content_hash=sha256(payload).hexdigest(),
        )
    )
    await db_session.flush()

    service = EmailIngestService(db_session, connection)
    raw = (
        b"From: a@globex.com\r\nTo: owner@acme.com\r\nMessage-ID: <pend@x>\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
        b"--B\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
        b"--B\r\nContent-Type: text/plain\r\n"
        b'Content-Disposition: attachment; filename="new.txt"\r\n\r\n' + payload + b"\r\n--B--\r\n"
    )
    await service.ingest_email(raw)

    fresh = (
        await db_session.execute(
            select(EmailAttachment).where(
                EmailAttachment.org_id == org, EmailAttachment.filename == "new.txt"
            )
        )
    ).scalar_one()
    assert fresh.extraction_status == "extracted"  # ran for real — never copied 'pending'
    assert fresh.extracted_text == "pending twin payload"


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
