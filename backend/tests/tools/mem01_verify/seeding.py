"""
Role: Deterministic synthetic corpora for the oracle's session probe database — a 1,000-email
      org large enough for every `minimum: 1000` corpus criterion, a six-email org with fully
      known leakage / language / snapshot expectations, and a two-email org reserved for the
      snapshot-isolation seal. Every expected number the DB-backed tests assert is recorded here
      at seeding time, computed from the spec, never read back from the database.
Used by: tests/tools/mem01_verify/conftest.py (probe_corpus factory) and the DB-backed modules.
Depends on: app.* ORM models (imported INSIDE the seeding function, only to arrange rows through
      the instrument's probe session planes); stdlib.
Key invariants:
  - Only reserved domains (`example.test`, `acme.test`, `partner.test`) and synthetic names.
  - Every subject/body/address carries a marker string that must never appear on stdout (R5).
  - The email `language` column is left NULL everywhere (the Stage A LANG baseline).
  - Seeding writes through the GLOBAL plane of the probe (the same plane the existing DB suites
    seed with); it never opens the configured database, and it verifies `current_database()`
    on its own connection before the first write (§16.11 test-side guard).
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

from tests.tools.mem01_verify.seeding_big import seed_big_org
from tests.tools.mem01_verify.seeding_rows import (
    ATTACHMENT_FILENAME_MARKER,
    BODY_MARKER,
    EXTERNAL_PARENT_ID,
    INLINE_IMAGE_HASH,
    ISO_ATTACHMENT_HASH,
    PERSONA_NAME_MARKER,
    SHARED_HASH_SMALL,
    SMALL_SUBJECT_MARKER,
    UNSUPPORTED_HASH,
    SeededCorpus,
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
    assert_probe_connection,
    flush_parents_first,
)


async def seed_small_org(session: object) -> SeededOrg:
    """Six emails whose leakage partition, language classes and snapshot artifacts are known."""
    org_id, connection_id = uuid4(), uuid4()
    await _register_org(session, org_id, "small")
    domain = "acme.test"
    persons = (uuid4(), uuid4(), uuid4())
    ids = tuple(uuid4() for _ in range(6))
    attachments = tuple(uuid4() for _ in range(5))
    p1_address, p1_alt, p2_address = f"a1@{domain}", f"a1.alt@{domain}", f"a2@{domain}"
    bcc_address = "blind@partner.test"
    when = _base_instant(0)
    rows: list[object] = [
        _connection(org_id, connection_id, f"mailbox@{domain}"),
        _person(org_id, persons[0], f"{PERSONA_NAME_MARKER} Small One"),
        _person(org_id, persons[1], f"{PERSONA_NAME_MARKER} Small Two"),
        _person(org_id, persons[2], f"{PERSONA_NAME_MARKER} Small Three"),
        _person_email(org_id, persons[0], p1_address),
        _person_email(org_id, persons[0], p1_alt),
        _person_email(org_id, persons[1], p2_address),
    ]
    body_two = f"{BODY_MARKER} two — „quoted“ text"
    emails = [
        _email(
            org_id,
            connection_id,
            ids[0],
            message_id=f"m1@{domain}",
            subject=f"{SMALL_SUBJECT_MARKER} one Δ",
            body_text=f"{BODY_MARKER} one … „first“",
            from_address=f"sender@{domain}",
            sent_at=when,
            received_at=when,
            has_attachments=True,
            headers={"Content-Language": "bg", "Date": "Mon, 07 Sep 2026 13:00:00 +0300"},
        ),
        _email(
            org_id,
            connection_id,
            ids[1],
            message_id=f"m2@{domain}",
            in_reply_to=f"m1@{domain}",
            references=[f"m1@{domain}"],
            subject=f"Re: {SMALL_SUBJECT_MARKER} one Δ",
            body_text=body_two,
            from_address=p1_address,
            sent_at=when + timedelta(hours=1),
            received_at=when + timedelta(hours=1, minutes=5),
            is_reply=True,
            has_attachments=True,
            headers={"Content-Language": "en-US"},
        ),
        _email(
            org_id,
            connection_id,
            ids[2],
            message_id=f"m3@{domain}",
            subject=None,
            body_text=None,
            from_address=f"sender@{domain}",
            sent_at=when,
            received_at=when,
            has_attachments=True,
            parse_status="failed",
            headers={"Content-Language": "de"},
        ),
        _email(
            org_id,
            connection_id,
            ids[3],
            message_id=f"m4@{domain}",
            references=[EXTERNAL_PARENT_ID],
            subject=f"{SMALL_SUBJECT_MARKER} four",
            body_text=f"{BODY_MARKER} four\r\nsecond line\u00a0end",
            from_address=f"sender@{domain}",
            sent_at=when,
            received_at=when,
            has_attachments=True,
            headers={},
        ),
        _email(
            org_id,
            connection_id,
            ids[4],
            message_id=f"m5@{domain}",
            in_reply_to=EXTERNAL_PARENT_ID,
            subject=f"{SMALL_SUBJECT_MARKER} five",
            body_text=f"{BODY_MARKER} five",
            from_address=f"sender@{domain}",
            sent_at=when,
            received_at=when,
            has_attachments=True,
            headers={"Content-Language": ""},
        ),
        _email(
            org_id,
            connection_id,
            ids[5],
            message_id=f"m1@{domain}",
            subject=f"{SMALL_SUBJECT_MARKER} six",
            body_text=body_two,
            from_address=f"sender@{domain}",
            sent_at=when,
            received_at=when,
            headers={"Content-Language": "bg-BG, en"},
        ),
    ]
    texts_by_artifact: dict[str, str | None] = {}
    for email in emails:
        texts_by_artifact[f"email_body:{email.id}"] = email.body_text  # type: ignore[attr-defined]
        texts_by_artifact[f"email_subject:{email.id}"] = email.subject  # type: ignore[attr-defined]
    texts_by_artifact[f"attachment_text:{attachments[0]}"] = f"{BODY_MARKER} attachment one"
    rows.extend(emails)
    rows.extend(
        [
            _recipient(org_id, connection_id, ids[0], "to", p1_address, persons[0]),
            _recipient(org_id, connection_id, ids[0], "cc", p2_address, persons[1]),
            _recipient(org_id, connection_id, ids[0], "bcc", bcc_address),
            _recipient(org_id, connection_id, ids[1], "to", f"sender@{domain}"),
            _recipient(org_id, connection_id, ids[3], "to", p1_address, persons[0]),
            _recipient(org_id, connection_id, ids[4], "to", p2_address, persons[1]),
            _attachment(
                org_id,
                connection_id,
                ids[0],
                attachments[0],
                filename=f"{ATTACHMENT_FILENAME_MARKER}-one.pdf",
                content_type="application/pdf",
                content_hash=SHARED_HASH_SMALL,
                extracted_text=f"{BODY_MARKER} attachment one",
                extraction_status="extracted",
                extractor_name="pdf",
                extractor_version="1",
            ),
            _attachment(
                org_id,
                connection_id,
                ids[3],
                attachments[1],
                filename=f"{ATTACHMENT_FILENAME_MARKER}-copy.pdf",
                content_type="application/pdf",
                content_hash=SHARED_HASH_SMALL,
                extracted_text=None,
                extraction_status="pending",
            ),
            _attachment(
                org_id,
                connection_id,
                ids[1],
                attachments[2],
                filename=f"{ATTACHMENT_FILENAME_MARKER}-logo.png",
                content_type="image/png",
                content_hash=INLINE_IMAGE_HASH,
                is_inline=True,
                extraction_status="skipped_nondocument",
            ),
            _attachment(
                org_id,
                connection_id,
                ids[4],
                attachments[3],
                filename=f"{ATTACHMENT_FILENAME_MARKER}-logo2.png",
                content_type="image/png",
                content_hash=INLINE_IMAGE_HASH,
                is_inline=True,
                extraction_status="skipped_nondocument",
            ),
            _attachment(
                org_id,
                connection_id,
                ids[2],
                attachments[4],
                filename=f"{ATTACHMENT_FILENAME_MARKER}-old.doc",
                content_type="application/msword",
                content_hash=UNSUPPORTED_HASH,
                extraction_status="unsupported_format",
            ),
            _grant(org_id, persons[0], ids[0], connection_id),
            _grant(org_id, persons[0], ids[1], connection_id),
            _grant(org_id, persons[0], ids[2], connection_id),
        ]
    )
    await flush_parents_first(session, rows)
    return SeededOrg(
        org_id=org_id,
        connection_id=connection_id,
        email_ids=ids,
        attachment_ids=attachments,
        person_ids=persons,
        email_count=6,
        attachment_count=5,
        attachments_with_text=1,
        null_body_email_ids=(ids[2],),
        null_subject_email_ids=(ids[2],),
        lang_class_by_email={
            ids[0]: "bg",
            ids[1]: "en",
            ids[2]: "other",
            ids[3]: "none",
            ids[4]: "none",
            ids[5]: "bg",
        },
        # reply E1-E2, collision E1-E6, template E2-E6, attachment E1-E4, sibling E4-E5;
        # E3 stands alone (its only attachment hash is unique, its headers join nothing).
        expected_groups=(frozenset({ids[0], ids[1], ids[3], ids[4], ids[5]}), frozenset({ids[2]})),
        personal_markers=(
            SMALL_SUBJECT_MARKER,
            BODY_MARKER,
            PERSONA_NAME_MARKER,
            ATTACHMENT_FILENAME_MARKER,
            p1_address,
            bcc_address,
        ),
        bcc_count=1,
        grant_count=3,
        not_ready_attachment_count=2,
        texts_by_artifact=texts_by_artifact,
    )


async def seed_iso_org(session: object) -> SeededOrg:
    """Two emails; the snapshot-isolation seal adds a third while a snapshot is open."""
    org_id, connection_id = uuid4(), uuid4()
    await _register_org(session, org_id, "iso")
    ids = (uuid4(), uuid4())
    when = _base_instant(0)
    person_id, attachment_id = uuid4(), uuid4()
    rows: list[object] = [
        _connection(org_id, connection_id, "mailbox@partner.test"),
        _person(org_id, person_id, f"{PERSONA_NAME_MARKER} Iso One"),
        _person_email(org_id, person_id, "iso1@partner.test"),
    ]
    texts_by_artifact: dict[str, str | None] = {}
    for index, email_id in enumerate(ids):
        subject, body = f"OracleIsoSubject {index}", f"{BODY_MARKER} iso {index}"
        texts_by_artifact[f"email_body:{email_id}"] = body
        texts_by_artifact[f"email_subject:{email_id}"] = subject
        rows.append(
            _email(
                org_id,
                connection_id,
                email_id,
                message_id=f"iso{index}@partner.test",
                subject=subject,
                body_text=body,
                from_address="sender@partner.test",
                sent_at=when,
                received_at=when,
                headers={"Content-Language": "en"},
            )
        )
    rows.append(
        _attachment(
            org_id,
            connection_id,
            ids[0],
            attachment_id,
            filename=f"{ATTACHMENT_FILENAME_MARKER}-iso.pdf",
            content_type="application/pdf",
            content_hash=ISO_ATTACHMENT_HASH,
            extracted_text=None,
            extraction_status="pending",
        )
    )
    rows.append(_grant(org_id, person_id, ids[0], connection_id))
    await flush_parents_first(session, rows)
    return SeededOrg(
        org_id=org_id,
        connection_id=connection_id,
        email_ids=ids,
        attachment_ids=(attachment_id,),
        person_ids=(person_id,),
        email_count=2,
        attachment_count=1,
        attachments_with_text=0,
        null_body_email_ids=(),
        null_subject_email_ids=(),
        lang_class_by_email={},
        expected_groups=(frozenset({ids[0]}), frozenset({ids[1]})),
        personal_markers=("OracleIsoSubject", PERSONA_NAME_MARKER, ATTACHMENT_FILENAME_MARKER),
        bcc_count=0,
        grant_count=1,
        not_ready_attachment_count=1,
        texts_by_artifact=texts_by_artifact,
    )


async def add_iso_email(session: object, org: SeededOrg, index: int) -> UUID:
    """Insert one more email into the iso org (used while a snapshot transaction is open)."""
    await assert_probe_connection(session)
    email_id = uuid4()
    when = _base_instant(index)
    session.add(
        _email(  # type: ignore[attr-defined]
            org.org_id,
            org.connection_id,
            email_id,
            message_id=f"iso-late{index}@partner.test",
            subject=f"OracleIsoSubject late {index}",
            body_text=f"{BODY_MARKER} iso late {index}",
            from_address="sender@partner.test",
            sent_at=when,
            received_at=when,
        )
    )
    await session.flush()  # type: ignore[attr-defined]
    return email_id


async def seed_corpus(sessions: object, database: str) -> SeededCorpus:
    """Seed the three orgs through the probe's global plane and commit."""
    async with sessions.global_() as session:  # type: ignore[attr-defined]
        bound = await assert_probe_connection(session)
        assert bound == database, (bound, database)
        big = await seed_big_org(session)
        small = await seed_small_org(session)
        iso = await seed_iso_org(session)
        await session.commit()
    return SeededCorpus(database=database, big=big, small=small, iso=iso)
