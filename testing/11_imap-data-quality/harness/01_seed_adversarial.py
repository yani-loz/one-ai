"""DQ seed — ingest a deliberately-nasty corpus through the REAL pipeline into a throwaway org.

Builds ~35 hand-crafted RFC822 messages, each targeting a specific DQ-* investigation point, and
ingests them via the real EmailIngestService (parse -> resolve -> store) so the measurement harnesses
can observe how the cleaning/resolution logic mangles known-bad input.

Isolation + safety:
  - Creates a RUN-STAMPED throwaway org (uuid4). NEVER touches the demo orgs (…0001/…0002) or the dev
    live org (d1500000…). Connections are marked `DQ-SEED <stamp>` so measurement + cleanup can find
    the org with no shared state passed between container invocations.
  - Two connections: a real-domain mailbox (owner@acme-gmbh.de) and a generic-domain mailbox
    (testfirm@gmx.de, for the DQ-D02 freemail-tenant case).
  - Cleanup is a separate harness (99_cleanup_seed.py) run AFTER measurement.

Run (testing/ is not mounted; pipe over stdin into the backend container):
    docker compose exec -T backend python - < testing/11_imap-data-quality/harness/01_seed_adversarial.py
"""
from __future__ import annotations

import asyncio
import base64
import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.connectors.imap.services.email_ingest_service import EmailIngestService
from app.connectors.models.connector_connection import ConnectorConnection
from app.connectors.security.credential_cipher import CredentialCipher
from app.core.config import get_settings
from app.core.database import GlobalSessionLocal

STAMP = uuid.uuid4().hex[:10]
ORG = uuid.uuid4()
NOW = datetime(2025, 6, 2, 10, 0, 0, tzinfo=UTC)

MAIL_REAL = "owner@acme-gmbh.de"      # real-domain tenant mailbox
MAIL_GMX = "testfirm@gmx.de"          # generic-domain tenant mailbox (DQ-D02)


def raw(headers: list[tuple[str, str]], body: bytes = b"") -> bytes:
    """Assemble exact wire bytes (CRLF) from header pairs + a raw body."""
    head = b"".join(f"{k}: {v}\r\n".encode() for k, v in headers)
    return head + b"\r\n" + body


def part(headers: list[tuple[str, str]], body: bytes) -> bytes:
    """One MIME part: its headers + a blank line + body (no trailing CRLF — caller adds it)."""
    return b"".join(f"{k}: {v}\r\n".encode() for k, v in headers) + b"\r\n" + body


def multipart(env: list[tuple[str, str]], parts: list[bytes], subtype: str, boundary: str) -> bytes:
    """Assemble a multipart message: envelope headers (incl. the Content-Type) + boundaried parts."""
    body = b""
    for p in parts:
        body += b"--" + boundary.encode() + b"\r\n" + p + b"\r\n"
    body += b"--" + boundary.encode() + b"--\r\n"
    return raw(env, body)


def b64(data: bytes) -> str:
    """base64 a payload for a Content-Transfer-Encoding: base64 part (decodes back exactly)."""
    return base64.b64encode(data).decode()


def deep_multipart(depth: int) -> bytes:
    """A ~`depth`-nested multipart that overflows the C stack at parse → RecursionError (DQ-F04)."""
    inner = part([("Content-Type", "text/plain")], b"deep body")
    msg = inner
    for i in range(depth):
        b = f"d{i}"
        msg = part(
            [("Content-Type", f'multipart/mixed; boundary="{b}"')],
            b"--" + b.encode() + b"\r\n" + msg + b"\r\n--" + b.encode() + b"--\r\n",
        )
    # Splice the envelope headers onto the outermost part (which already carries its Content-Type).
    env = (
        b"From: deepnest@firma-x.de\r\n"
        b"To: owner@acme-gmbh.de\r\n"
        b"Subject: DQ-F04 deep nest\r\n"
        b"Message-ID: <deep-f04@seed>\r\n"
        b"Date: Mon, 02 Jun 2025 10:00:00 +0000\r\n"
    )
    return env + msg


def build_corpus() -> list[tuple[str, bytes, datetime | None, str]]:
    """Return (mailbox, raw_bytes, internal_date, dq_tag) for every adversarial message."""
    msgs: list[tuple[str, bytes, datetime | None, str]] = []

    def add(mailbox: str, headers: list[tuple[str, str]], body: bytes, tag: str,
            idate: datetime | None = NOW) -> None:
        msgs.append((mailbox, raw(headers, body), idate, tag))

    base_to = ("To", "owner@acme-gmbh.de")

    # — DQ-B01 Gmail dot/+subaddress → 3 distinct persons for one human —
    for i, frm in enumerate(("j.smith@gmail.com", "jsmith@gmail.com", "j.smith+news@gmail.com")):
        add(MAIL_REAL, [("From", f"John Smith <{frm}>"), base_to,
                        ("Subject", "B01"), ("Message-ID", f"<b01-{i}@seed>"),
                        ("Date", "Mon, 02 Jun 2025 09:00:00 +0000")], b"hi", "B01")

    # — DQ-B02 same human, work+personal (no name merge) → 2 persons same display_name —
    for i, frm in enumerate(("anna.berg@kunde-gmbh.de", "anna.berg@gmail.com")):
        add(MAIL_REAL, [("From", f"Anna Berg <{frm}>"), base_to,
                        ("Subject", "B02"), ("Message-ID", f"<b02-{i}@seed>")], b"hi", "B02")

    # — DQ-B03 company subdomain fragmentation → lieferant.de + mail.lieferant.de —
    for i, frm in enumerate(("peter@lieferant.de", "peter@mail.lieferant.de")):
        add(MAIL_REAL, [("From", f"Peter Klein <{frm}>"), base_to,
                        ("Subject", "B03"), ("Message-ID", f"<b03-{i}@seed>")], b"hi", "B03")

    # — DQ-B05 duplicate message: same logical mail, 2nd byte-differs → 2 rows, same Message-ID —
    dup_headers = [("From", "Lars Dup <lars@firma-y.de>"), base_to, ("Subject", "B05 invoice"),
                   ("Message-ID", "<b05-dup@seed>"), ("Date", "Mon, 02 Jun 2025 08:00:00 +0000")]
    add(MAIL_REAL, dup_headers, b"same body", "B05")
    add(MAIL_REAL, dup_headers + [("X-Refetch-Marker", "folder2")], b"same body", "B05")

    # — DQ-B06 duplicate recipient edges: same addr twice in To + again in Cc —
    add(MAIL_REAL, [("From", "Boss <boss@acme-gmbh.de>"),
                    ("To", "kunde1@firma-z.de, kunde1@firma-z.de"),
                    ("Cc", "kunde1@firma-z.de"),
                    ("Subject", "B06"), ("Message-ID", "<b06@seed>")], b"hi", "B06")

    # — DQ-C01 automated/list senders still mint a person+company —
    add(MAIL_REAL, [("From", "Brand Updates <updates@news.brand.test>"), base_to,
                    ("Subject", "C01a newsletter"), ("Message-ID", "<c01a@seed>"),
                    ("List-Id", "Brand News <news.brand.test>"),
                    ("List-Unsubscribe", "<mailto:u@news.brand.test>")], b"promo", "C01")
    add(MAIL_REAL, [("From", "Shop <mailings@shop.test>"), base_to,
                    ("Subject", "C01b bulk"), ("Message-ID", "<c01b@seed>"),
                    ("Precedence", "bulk")], b"promo", "C01")

    # — DQ-C02 reply_to / Sender become people (routing identities) —
    add(MAIL_REAL, [("From", "Real Person <realperson@partner.test>"), base_to,
                    ("Reply-To", "track-7f3a@reply.mailer.test"),
                    ("Sender", "relay-99@mta.route.test"),
                    ("Subject", "C02"), ("Message-ID", "<c02@seed>")], b"hi", "C02")

    # — DQ-C03 typo domain → bogus company gmial.com (misses freemail filter) —
    add(MAIL_REAL, [("From", "John Typo <john@gmial.com>"), base_to,
                    ("Subject", "C03"), ("Message-ID", "<c03@seed>")], b"hi", "C03")

    # — DQ-C04 IP-literal + single-label domain companies —
    add(MAIL_REAL, [("From", "Sysop <sysop@[10.0.0.5]>"), base_to,
                    ("Subject", "C04a"), ("Message-ID", "<c04a@seed>")], b"hi", "C04")
    add(MAIL_REAL, [("From", "Bob Local <bob@localhost>"), base_to,
                    ("Subject", "C04b"), ("Message-ID", "<c04b@seed>")], b"hi", "C04")

    # — DQ-D01 role address suppresses the COMPANY too (high-volume DACH counterparties lost) —
    for i, frm in enumerate(("kontakt@kunde-gross.test", "buchhaltung@kunde-gross.test",
                             "info@kunde-gross.test")):
        add(MAIL_REAL, [("From", f"Kunde <{frm}>"), base_to,
                        ("Subject", "D01"), ("Message-ID", f"<d01-{i}@seed>")], b"hi", "D01")
    # role address as a RECIPIENT (its domain also lost from the company graph)
    add(MAIL_REAL, [("From", "Owner <owner@acme-gmbh.de>"),
                    ("To", "info@grosskunde.test"),
                    ("Subject", "D01 recip"), ("Message-ID", "<d01-r@seed>")], b"hi", "D01")

    # — DQ-E01/E02 internal stamping + mailbox-centric direction —
    add(MAIL_REAL, [("From", "Owner <owner@acme-gmbh.de>"), ("To", "anna@acme-gmbh.de"),
                    ("Subject", "E01 outbound"), ("Message-ID", "<e01-out@seed>")], b"hi", "E01")
    add(MAIL_REAL, [("From", "Anna Intern <anna@acme-gmbh.de>"), base_to,
                    ("Subject", "E02 inbound-internal"), ("Message-ID", "<e02-in@seed>")], b"hi", "E02")

    # — DQ-F01 dirty display_name: RFC2047, address-as-name, group syntax —
    add(MAIL_REAL, [("From", "=?UTF-8?Q?M=C3=BCller=2C_Anna?= <mueller.anna@firma-x.de>"), base_to,
                    ("Subject", "F01a rfc2047 name"), ("Message-ID", "<f01a@seed>")], b"hi", "F01")
    add(MAIL_REAL, [("From", "anna3@firma-x.de <anna3@firma-x.de>"), base_to,
                    ("Subject", "F01b addr-as-name"), ("Message-ID", "<f01b@seed>")], b"hi", "F01")
    add(MAIL_REAL, [("From", "Sender <sender@firma-x.de>"), ("To", "undisclosed-recipients:;"),
                    ("Subject", "F01c group recip"), ("Message-ID", "<f01c@seed>")], b"hi", "F01")

    # — DQ-F02 body mojibake: double-encoded umlauts declared utf-8 → 'GrÃ¼ÃŸe' —
    mojibake = "Grüße aus München".encode("utf-8").decode("latin-1").encode("utf-8")
    add(MAIL_REAL, [("From", "Mojibake <mojibake@firma-x.de>"), base_to,
                    ("Subject", "F02"), ("Message-ID", "<f02@seed>"),
                    ("Content-Type", 'text/plain; charset="utf-8"')], mojibake, "F02")

    # — DQ-F03 plain-part-wins hides rich HTML (word_count<5, size>50KB) —
    big_html = b"<html><body>" + (b"<p>Wichtiger Vertragstext Absatz.</p>" * 1600) + b"</body></html>"
    f03 = multipart(
        [("From", "Campaign <campaign@marketing-x.test>"), base_to,
         ("Subject", "F03 alt"), ("Message-ID", "<f03@seed>"),
         ("Content-Type", 'multipart/alternative; boundary="alt0"')],
        [part([("Content-Type", "text/plain")], b"Bitte HTML aktivieren."),
         part([("Content-Type", "text/html")], big_html)],
        "alternative", "alt0")
    msgs.append((MAIL_REAL, f03, NOW, "F03"))

    # — DQ-F04 deep-nested multipart → RecursionError → parse_status='failed' —
    msgs.append((MAIL_REAL, deep_multipart(350), NOW, "F04"))

    # — DQ-F06 date sanity: epoch + far-future Date vs received_at=NOW —
    add(MAIL_REAL, [("From", "Epoch <epoch@firma-x.de>"), base_to, ("Subject", "F06a"),
                    ("Message-ID", "<f06a@seed>"),
                    ("Date", "Thu, 01 Jan 1970 00:00:00 +0000")], b"hi", "F06")
    add(MAIL_REAL, [("From", "Future <future@firma-x.de>"), base_to, ("Subject", "F06b"),
                    ("Message-ID", "<f06b@seed>"),
                    ("Date", "Wed, 01 Jan 2099 00:00:00 +0000")], b"hi", "F06")

    # — DQ-F07 subject with a control char (BEL) —
    add(MAIL_REAL, [("From", "Ctrl <ctrl@firma-x.de>"), base_to,
                    ("Subject", "Rechnung\x07Maerz"), ("Message-ID", "<f07@seed>")], b"hi", "F07")

    # — DQ-G01 Message-ID collision: 2 DIFFERENT emails share one id → thread over-merge —
    add(MAIL_REAL, [("From", "Alice <alice@firma-x.de>"), base_to, ("Subject", "Invoice 1"),
                    ("Message-ID", "<collide-g01@seed>")], b"first content", "G01")
    add(MAIL_REAL, [("From", "Eve <eve@attacker.test>"), base_to, ("Subject", "Totally different"),
                    ("Message-ID", "<collide-g01@seed>")], b"second content", "G01")

    # — DQ-G02 dangling references (point at ids absent from the corpus) —
    add(MAIL_REAL, [("From", "Replyer <replyer@firma-x.de>"), base_to, ("Subject", "Re: ghost"),
                    ("Message-ID", "<g02@seed>"),
                    ("In-Reply-To", "<ghost@void.test>"),
                    ("References", "<ghost@void.test> <ghost2@void.test>")], b"reply", "G02")

    # — DQ-H01 binary PDF attachment → extracted_text NULL, bytes dropped —
    h01 = multipart(
        [("From", "Doc Sender <docsender@firma-x.de>"), base_to,
         ("Subject", "H01 pdf"), ("Message-ID", "<h01@seed>"),
         ("Content-Type", 'multipart/mixed; boundary="m1"')],
        [part([("Content-Type", "text/plain")], b"see attached"),
         part([("Content-Type", "application/pdf"), ("Content-Disposition", 'attachment; filename="vertrag.pdf"'),
               ("Content-Transfer-Encoding", "base64")], b64(b"%PDF-1.4 fake pdf bytes").encode())],
        "mixed", "m1")
    msgs.append((MAIL_REAL, h01, NOW, "H01"))

    # — DQ-H02 latin-1 CSV attachment → utf-8 decode → mojibake/replacement in extracted_text —
    csv_latin1 = "Name;Stadt\nMüller;München\n".encode("latin-1")
    h02 = multipart(
        [("From", "Csv Sender <csvsender@firma-x.de>"), base_to,
         ("Subject", "H02 csv"), ("Message-ID", "<h02@seed>"),
         ("Content-Type", 'multipart/mixed; boundary="m2"')],
        [part([("Content-Type", "text/plain")], b"csv attached"),
         part([("Content-Type", 'text/csv; charset="latin-1"'),
               ("Content-Disposition", 'attachment; filename="export.csv"'),
               ("Content-Transfer-Encoding", "base64")], b64(csv_latin1).encode())],
        "mixed", "m2")
    msgs.append((MAIL_REAL, h02, NOW, "H02"))

    # — DQ-H03 attachment RFC2047 filename + a zero-byte attachment —
    h03 = multipart(
        [("From", "Att Meta <attmeta@firma-x.de>"), base_to,
         ("Subject", "H03"), ("Message-ID", "<h03@seed>"),
         ("Content-Type", 'multipart/mixed; boundary="m3"')],
        [part([("Content-Type", "text/plain")], b"meta"),
         part([("Content-Type", "application/octet-stream"),
               ("Content-Disposition",
                'attachment; filename="=?UTF-8?Q?Rechnung_M=C3=A4rz.pdf?="')], b"")],
        "mixed", "m3")
    msgs.append((MAIL_REAL, h03, NOW, "H03"))

    # — DQ-I04 headers JSONB bloat + sensitive/security headers —
    add(MAIL_REAL, [("From", "Hdr <hdr@firma-x.de>"), base_to, ("Subject", "I04"),
                    ("Message-ID", "<i04@seed>"),
                    ("Received", "from a.test by b.test; Mon, 02 Jun 2025 10:00:00 +0000"),
                    ("Received", "from c.test by d.test; Mon, 02 Jun 2025 09:59:00 +0000"),
                    ("DKIM-Signature", "v=1; a=rsa-sha256; d=firma-x.de; s=sel; b=AAAA" + "B" * 200),
                    ("Authentication-Results", "mx.test; spf=pass; dkim=pass"),
                    ("X-Spam-Score", "0.1"), ("X-Originating-IP", "[203.0.113.9]")], b"hi", "I04")

    # — DQ-D02 generic-domain tenant (gmx.de): own company never forms, colleague not internal —
    add(MAIL_GMX, [("From", "Testfirm <testfirm@gmx.de>"), ("To", "partner@gmx.de"),
                   ("Subject", "D02 outbound"), ("Message-ID", "<d02-out@seed>")], b"hi", "D02")
    add(MAIL_GMX, [("From", "Partner <partner@gmx.de>"), ("To", "testfirm@gmx.de"),
                   ("Subject", "D02 inbound"), ("Message-ID", "<d02-in@seed>")], b"hi", "D02")

    return msgs


async def get_or_create_connection(session, mailbox: str) -> ConnectorConnection:
    cipher = CredentialCipher(get_settings().connector_secret_key, require_secure=False)
    conn = ConnectorConnection(
        org_id=ORG, connector_type="imap",
        display_name=f"DQ-SEED {STAMP} {mailbox}",
        auth_method="app_password", username=mailbox,
        config={"host": "dq-seed", "port": 0, "use_ssl": False},
        secret_ciphertext=cipher.encrypt("dq-seed-placeholder"),
        secret_key_version=cipher.key_version, status="configured",
    )
    session.add(conn)
    await session.flush()
    return conn


async def main() -> None:
    corpus = build_corpus()
    tally: dict[str, int] = {"stored": 0, "skipped": 0, "failed": 0}
    async with GlobalSessionLocal() as session:
        services: dict[str, EmailIngestService] = {}
        for mailbox in (MAIL_REAL, MAIL_GMX):
            conn = await get_or_create_connection(session, mailbox)
            services[mailbox] = EmailIngestService(session, conn)
        await session.commit()

        for mailbox, rawbytes, idate, tag in corpus:
            try:
                outcome = await services[mailbox].ingest_email(rawbytes, idate)
                await session.commit()
                tally[outcome.value] += 1
            except IntegrityError as exc:
                await session.rollback()
                tally["skipped" if "uq_email_message_dedup" in str(exc.orig) else "failed"] += 1
            except Exception as exc:  # noqa: BLE001 - one bad seed message must not abort the seed
                await session.rollback()
                tally["failed"] += 1
                print(f"  [seed-fail] {type(exc).__name__} @ {tag}", flush=True)

        # Per-table census of the seed org (proves the seed landed as expected).
        print(f"SEED ORG = {ORG}  (marker: DQ-SEED {STAMP})")
        print(f"ingest tally: {tally}  (corpus size {len(corpus)})")
        for tbl in ("connector_connection", "email_message", "email_recipient", "email_attachment",
                    "person", "person_email", "company", "company_domain", "person_company"):
            n = await session.execute(
                text(f"SELECT count(*) FROM {tbl} WHERE org_id = :o"), {"o": str(ORG)}
            )
            print(f"  {tbl:22} {n.scalar()}")


asyncio.run(main())
