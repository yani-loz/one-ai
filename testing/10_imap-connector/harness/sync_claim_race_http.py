"""TC-IM-B01 — Two concurrent POST /connectors/{id}/sync: exactly one 202 + one 409.

The conditional-UPDATE claim (claim_for_sync) is the only gate against a double sync. Fire two
concurrent triggers at the LIVE server (in-container httpx) with a forged company_admin token and
assert exactly one 202 + one 409 + exactly ONE 'running' ledger row (the loser raises
SyncAlreadyRunningError before start_run, so it inserts no ledger row).

Run (testing/ is NOT mounted; pipe over stdin into the backend container):
    docker compose exec -T backend python - < testing/10_imap-connector/harness/sync_claim_race_http.py

This spawns a REAL background runner in the live process. The seeded connection points at an
UNROUTABLE host so the runner fails fast (no real IMAP). Token is minted in-container with the
server's own JWT secret + connector cipher, so it round-trips. Safety: a RUN-STAMPED throwaway org;
cleanup deletes only that org's connector_connection (CASCADE → cursor/run/email). The still-in-
flight runner's fenced finalize hits 0 rows after the delete → no-op.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from uuid import uuid4

import httpx
from sqlalchemy import delete, func, select

from app.connectors.models.connector_connection import ConnectorConnection
from app.connectors.models.connector_sync_run import ConnectorSyncRun
from app.connectors.security.credential_cipher import CredentialCipher
from app.core.config import get_settings
from app.core.database import GlobalSessionLocal
from app.identity.principal import Principal
from app.identity.security.tokens import COMPANY_AUDIENCE, encode_access_token

logging.disable(logging.CRITICAL)

S = get_settings()
ORG = uuid4()
USER = uuid4()
BASE = "http://localhost:8000"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} :: {detail}")


def forge_token() -> str:
    """Mint a company_admin access token for the throwaway org with the server's own secret."""
    principal = Principal(subject_id=USER, org_id=ORG, role="company_admin", subject_type="user")
    return encode_access_token(principal, timedelta(minutes=15), COMPANY_AUDIENCE)


async def seed() -> str:
    """Insert a connection on the throwaway org with an UNROUTABLE host; return its id (str)."""
    cipher = CredentialCipher(S.connector_secret_key, require_secure=S.requires_secure_secrets)
    async with GlobalSessionLocal() as session:
        connection = ConnectorConnection(
            org_id=ORG,
            connector_type="imap",
            display_name="B01 race mailbox",
            auth_method="app_password",
            username="b01@example.test",
            # 192.0.2.0/24 is TEST-NET-1 (RFC 5737) — guaranteed unroutable, runner fails fast.
            config={"host": "192.0.2.1", "port": 993, "use_ssl": True},
            secret_ciphertext=cipher.encrypt("app-password"),
            secret_key_version=cipher.key_version,
            status="configured",
        )
        session.add(connection)
        await session.flush()
        cid = str(connection.id)
        await session.commit()
    return cid


async def running_ledger_rows(connection_id: str) -> int:
    async with GlobalSessionLocal() as session:
        return (
            await session.execute(
                select(func.count())
                .select_from(ConnectorSyncRun)
                .where(
                    ConnectorSyncRun.connection_id == connection_id,
                    ConnectorSyncRun.status == "running",
                )
            )
        ).scalar_one()


async def total_ledger_rows(connection_id: str) -> int:
    async with GlobalSessionLocal() as session:
        return (
            await session.execute(
                select(func.count())
                .select_from(ConnectorSyncRun)
                .where(ConnectorSyncRun.connection_id == connection_id)
            )
        ).scalar_one()


async def cleanup() -> None:
    async with GlobalSessionLocal() as session:
        await session.execute(delete(ConnectorConnection).where(ConnectorConnection.org_id == ORG))
        await session.commit()
    print(f"cleanup: deleted connector_connection rows for org {ORG} (CASCADE)")


async def main() -> None:
    cid = await seed()
    headers = {"Authorization": f"Bearer {forge_token()}"}
    print(f"seeded connection {cid} on throwaway org {ORG}")
    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as client:
            # Fire two concurrent sync triggers.
            r1, r2 = await asyncio.gather(
                client.post(f"/connectors/{cid}/sync", headers=headers),
                client.post(f"/connectors/{cid}/sync", headers=headers),
            )
        codes = sorted([r1.status_code, r2.status_code])
        exactly_one_each = codes == [202, 409]
        check(
            "B01_concurrent_sync_one_202_one_409",
            exactly_one_each,
            f"status_codes={codes} (expected [202, 409])",
        )

        # The 409 loser raised before start_run → inserts NO ledger row; exactly one 'running'.
        # (Give the spawned runner a beat to attempt its first fenced write / fail fast.)
        await asyncio.sleep(2)
        running = await running_ledger_rows(cid)
        total = await total_ledger_rows(cid)
        check(
            "B01_exactly_one_running_ledger_row",
            running <= 1 and total == 1,
            f"running_ledger_rows={running} total_ledger_rows={total} "
            f"(loser inserts none → total must be 1; running may already be finalized)",
        )

        # Confirm the 409 carries the 'already running' message, not a 500.
        body_409 = (r1 if r1.status_code == 409 else r2).text
        check(
            "B01_409_is_clean_conflict_not_500",
            "running" in body_409.lower() or "already" in body_409.lower(),
            f"409_body={body_409[:120]}",
        )
    finally:
        await cleanup()

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\nRESULT: {passed}/{len(results)} checks passed")


asyncio.run(main())
