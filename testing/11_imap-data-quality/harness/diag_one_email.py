"""Diagnostic: ingest a few REAL .eml files one at a time and print the FULL error.

The disk-ingest driver swallows the real DB error (prints only the exception type), so a real
email failing with IntegrityError tells us nothing about WHICH constraint. This reproduces it with
a full traceback + the underlying asyncpg error, against a throwaway org. Run with the spikes mount:
    docker compose run --rm -T -v "<host>/spikes:/spikes:ro" backend python - < this_file
"""
from __future__ import annotations

import asyncio
import traceback
import uuid
from pathlib import Path

from app.connectors.imap.services.email_ingest_service import EmailIngestService
from app.connectors.models.connector_connection import ConnectorConnection
from app.connectors.security.credential_cipher import CredentialCipher
from app.core.config import get_settings
from app.core.database import GlobalSessionLocal

ORG = uuid.uuid4()
MAILBOX = "yani.lozanov@ethera-tech.com"


async def main() -> None:
    all_files = sorted(Path("/spikes/imap_dump").rglob("*.eml"))
    # Replicate the driver's even-sample selection (files[::step]) so we hit the SAME first failure.
    files = all_files[:: max(1, len(all_files) // 3000)][:400]
    print(f"probing {len(files)} real emails (even sample) into throwaway org {ORG}")
    async with GlobalSessionLocal() as session:
        cipher = CredentialCipher(get_settings().connector_secret_key, require_secure=False)
        conn = ConnectorConnection(
            org_id=ORG, connector_type="imap", display_name="DQ-DIAG", auth_method="app_password",
            username=MAILBOX, config={"host": "diag", "port": 0, "use_ssl": False},
            secret_ciphertext=cipher.encrypt("x"), secret_key_version=cipher.key_version,
            status="configured",
        )
        session.add(conn)
        await session.commit()
        service = EmailIngestService(session, conn)

        ok = 0
        for path in files:
            raw = path.read_bytes()
            try:
                outcome = await service.ingest_email(raw)
                await session.commit()
                ok += 1
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                print(f"  FIRST FAILURE after {ok} OK: {type(exc).__name__} @ {path.name} ({len(raw)} bytes)")
                orig = getattr(exc, "orig", None)
                if orig is not None:
                    print(f"      orig: {type(orig).__name__}: {str(orig)[:400]}")
                print("      --- full traceback (tail) ---")
                print("      " + "".join(traceback.format_exception(exc))[-1500:])
                print(f"      --- offending email head ---\n      {raw[:300]!r}")
                break
        else:
            print(f"  all {ok} OK (no failure reproduced)")
        print(f"total OK before stop: {ok}")
        # cleanup
        from sqlalchemy import text
        for t in ("email_recipient", "email_attachment", "email_message", "person_company",
                  "person_email", "person_alias", "person", "company_domain", "company",
                  "connector_connection"):
            await session.execute(text(f"DELETE FROM {t} WHERE org_id=:o"), {"o": str(ORG)})
        await session.commit()
        print("cleaned up diag org")


asyncio.run(main())
