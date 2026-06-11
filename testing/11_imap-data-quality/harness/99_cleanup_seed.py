"""DQ cleanup — delete ONLY the DQ-SEED throwaway org(s); never touch LIVE or demo orgs.

Finds every org whose connector_connection is marked `DQ-SEED` and deletes its rows from every graph
table (children first; FKs would cascade, but explicit is safe). The LIVE dev org (d1500000…) and the
demo orgs (…0001/…0002) are never selected. Idempotent.

Run: docker compose exec -T backend python - < testing/11_imap-data-quality/harness/99_cleanup_seed.py
"""
from __future__ import annotations

import asyncio

import asyncpg

from app.core.config import get_settings

S = get_settings()
GLOBAL = (
    f"postgresql://{S.global_db_user}:{S.oneai_global_password}"
    f"@{S.postgres_host}:{S.postgres_port}/{S.postgres_db}"
)
DEV_ORG = "d1500000-0000-0000-0000-000000000001"
DEMO = ("00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002")
TABLES = [
    "email_recipient", "email_attachment", "email_message",
    "person_company", "person_email", "person_alias", "person",
    "company_domain", "company", "connector_connection",
]


async def main() -> None:
    conn = await asyncpg.connect(GLOBAL)
    try:
        seed_orgs = [
            str(r["org_id"])
            for r in await conn.fetch(
                "SELECT DISTINCT org_id FROM connector_connection WHERE display_name LIKE 'DQ-SEED%'"
            )
        ]
        # Hard safety: never delete LIVE or demo orgs even if somehow matched.
        seed_orgs = [o for o in seed_orgs if o != DEV_ORG and o not in DEMO]
        if not seed_orgs:
            print("no DQ-SEED orgs found — nothing to clean.")
            return
        print(f"deleting {len(seed_orgs)} seed org(s): {seed_orgs}")
        for table in TABLES:
            res = await conn.execute(
                f"DELETE FROM {table} WHERE org_id = ANY($1::uuid[])", seed_orgs
            )
            print(f"  {table:22} {res}")
        # Confirm LIVE untouched.
        live = await conn.fetchval("SELECT count(*) FROM email_message WHERE org_id=$1", DEV_ORG)
        print(f"LIVE org email_message still present: {live}")
    finally:
        await conn.close()


asyncio.run(main())
