"""
Role: The 1,000-email synthetic org of the oracle's probe corpus — large enough for every
      `minimum: 1000` corpus criterion, with reply chains, a reused Message-ID, a shared
      attachment hash, template bodies, quote markers, Date headers with and without a zone,
      and not-ready attachments (unsupported, pending) beside excluded inline images.
Used by: tests.tools.mem01_verify.seeding.seed_corpus.
Depends on: tests.tools.mem01_verify.seeding_rows (builders, markers, SeededOrg).
Key invariants:
  - The expected numbers the gate seals assert (1040 language items, 1055 required logical
    items, 15 not-ready inputs, 5 exclusions, 2040 text artifacts) follow from this spec.
"""

from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
from uuid import UUID, uuid4

from tests.tools.mem01_verify.seeding_rows import (
    ATTACHMENT_FILENAME_MARKER,
    BIG_SUBJECT_MARKER,
    BODY_MARKER,
    INLINE_IMAGE_HASH,
    PERSONA_NAME_MARKER,
    SHARED_HASH_SMALL,
    UNSUPPORTED_HASH,
    SeededOrg,
    _attachment,
    _base_instant,
    _connection,
    _email,
    _grant,
    _person,
    _person_email,
    _recipient,
    _register_org,
    flush_parents_first,
)


async def seed_big_org(session: object, email_total: int = 1000) -> SeededOrg:
    """A 1,000-email org: enough rows for every `minimum: 1000` criterion, with not-ready inputs."""
    org_id, connection_id = uuid4(), uuid4()
    await _register_org(session, org_id, "big")
    domain = "example.test"
    persons = (uuid4(), uuid4())
    reader_address, cc_address = f"b1@{domain}", f"b2@{domain}"
    ids = tuple(uuid4() for _ in range(email_total))
    rows: list[object] = [
        _connection(org_id, connection_id, f"mailbox@{domain}"),
        _person(org_id, persons[0], f"{PERSONA_NAME_MARKER} Big One"),
        _person(org_id, persons[1], f"{PERSONA_NAME_MARKER} Big Two"),
        _person_email(org_id, persons[0], reader_address),
        _person_email(org_id, persons[1], cc_address),
    ]
    lang_class: dict[UUID, str] = {}
    bcc_count = 0
    for index, email_id in enumerate(ids):
        headers: dict[str, str] = {}
        bucket = index % 10
        if bucket in (0, 1, 2):
            headers["Content-Language"] = "bg"
            lang_class[email_id] = "bg"
        elif bucket in (3, 4):
            headers["Content-Language"] = "en-GB"
            lang_class[email_id] = "en"
        elif bucket == 5:
            headers["Content-Language"] = "de"
            lang_class[email_id] = "other"
        else:
            lang_class[email_id] = "none"
        when = _base_instant(index % 24)
        if index < 100:
            headers["Date"] = (when + timedelta(hours=3)).strftime("%a, %d %b %Y %H:%M:%S +0300")
        elif index < 200:
            headers["Date"] = when.strftime("%a, %d %b %Y %H:%M:%S")
        message_id = f"big{index}@{domain}"
        in_reply_to = f"big{index - 1}@{domain}" if 10 <= index < 30 else None
        if index == 41:
            message_id = f"big40@{domain}"  # reused Message-ID (collision with index 40)
        body = f"{BODY_MARKER} {index} Тяло на съобщението"
        if index % 50 == 0:
            body += "\n> quoted line\nOn Monday, someone wrote:"
        if index in (60, 61):
            body = f"{BODY_MARKER} identical template body"
        rows.append(
            _email(
                org_id,
                connection_id,
                email_id,
                message_id=message_id,
                in_reply_to=in_reply_to,
                references=[in_reply_to] if in_reply_to else None,
                subject=f"{BIG_SUBJECT_MARKER} {index} Ω",
                body_text=body,
                from_address=f"sender{index % 7}@partner.test",
                sent_at=when,
                received_at=when if index < 300 else when + timedelta(minutes=2),
                is_reply=in_reply_to is not None,
                has_attachments=index < 60,
                direction="inbound" if index % 2 else "outbound",
                headers=headers,
                size_bytes=2048,
            )
        )
        rows.append(_recipient(org_id, connection_id, email_id, "to", reader_address, persons[0]))
        if index % 7 == 0:
            rows.append(_recipient(org_id, connection_id, email_id, "cc", cc_address, persons[1]))
        if index % 13 == 0:
            rows.append(_recipient(org_id, connection_id, email_id, "bcc", "hidden@partner.test"))
            bcc_count += 1
    attachments: list[UUID] = []
    with_text = 0
    not_ready = 0
    for index in range(60):
        attachment_id = uuid4()
        attachments.append(attachment_id)
        email_id = ids[index]
        if index < 40:
            # indexes 30-32 share one hash (an attachment edge over three carriers)
            content_hash = (
                SHARED_HASH_SMALL
                if index in (30, 31, 32)
                else sha256(f"big-att-{index}".encode()).hexdigest()
            )
            rows.append(
                _attachment(
                    org_id,
                    connection_id,
                    email_id,
                    attachment_id,
                    filename=f"{ATTACHMENT_FILENAME_MARKER}-{index}.pdf",
                    content_type="application/pdf",
                    content_hash=content_hash,
                    extracted_text=f"{BODY_MARKER} attachment {index} текст",
                    extraction_status="extracted",
                    extractor_name="pdf",
                    extractor_version="1",
                )
            )
            with_text += 1
        elif index < 50:
            rows.append(
                _attachment(
                    org_id,
                    connection_id,
                    email_id,
                    attachment_id,
                    filename=f"{ATTACHMENT_FILENAME_MARKER}-{index}.doc",
                    content_type="application/msword",
                    content_hash=UNSUPPORTED_HASH,
                    extraction_status="unsupported_format",
                )
            )
            not_ready += 1
        elif index < 55:
            rows.append(
                _attachment(
                    org_id,
                    connection_id,
                    email_id,
                    attachment_id,
                    filename=f"{ATTACHMENT_FILENAME_MARKER}-{index}.png",
                    content_type="image/png",
                    content_hash=INLINE_IMAGE_HASH,
                    is_inline=True,
                    extraction_status="skipped_nondocument",
                )
            )
        else:
            rows.append(
                _attachment(
                    org_id,
                    connection_id,
                    email_id,
                    attachment_id,
                    filename=f"{ATTACHMENT_FILENAME_MARKER}-{index}.pdf",
                    content_type="application/pdf",
                    content_hash=sha256(f"big-pending-{index}".encode()).hexdigest(),
                    extraction_status="pending",
                )
            )
            not_ready += 1
    grants = 0
    for index in range(100):
        rows.append(_grant(org_id, persons[0], ids[index], connection_id))
        grants += 1
    for index in range(10):
        revoked = _base_instant(1) if index < 5 else None
        rows.append(_grant(org_id, persons[1], ids[index], connection_id, revoked_at=revoked))
        grants += 1
    await flush_parents_first(session, rows)
    return SeededOrg(
        org_id=org_id,
        connection_id=connection_id,
        email_ids=ids,
        attachment_ids=tuple(attachments),
        person_ids=persons,
        email_count=email_total,
        attachment_count=60,
        attachments_with_text=with_text,
        null_body_email_ids=(),
        null_subject_email_ids=(),
        lang_class_by_email=lang_class,
        expected_groups=(),
        personal_markers=(
            BIG_SUBJECT_MARKER,
            BODY_MARKER,
            PERSONA_NAME_MARKER,
            ATTACHMENT_FILENAME_MARKER,
            reader_address,
            "hidden@partner.test",
            "Тяло на съобщението",
        ),
        bcc_count=bcc_count,
        grant_count=grants,
        not_ready_attachment_count=not_ready,
    )
