"""DQ census gate — row-count the entity graph per org (read-only).

Decides whether LIVE rates are meaningful (standard's first-step gate). Lists every org that owns
graph rows so we never touch the demo orgs (…0001/…0002) by mistake. Uses the GLOBAL (BYPASSRLS)
engine — the same engine the dev disk-ingest used — so it sees the dev-org rows without a GUC.

Run (testing/ is not mounted; pipe over stdin into the backend container):
    docker compose exec -T backend python - < testing/11_imap-data-quality/harness/00_corpus_census.py

Non-destructive: pure SELECT/count. Touches no rows.
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

GRAPH_TABLES = [
    "connector_connection",
    "email_message",
    "email_recipient",
    "email_attachment",
    "person",
    "person_email",
    "person_alias",
    "company",
    "company_domain",
    "person_company",
]


async def main() -> None:
    conn = await asyncpg.connect(GLOBAL)
    try:
        print(f"=== Per-org row counts (dev org = {DEV_ORG}) ===")
        # Which orgs own graph rows at all (so we know what's in the DB and avoid the demo orgs).
        for table in ("email_message", "person", "company", "connector_connection"):
            rows = await conn.fetch(
                f"SELECT org_id, count(*) AS n FROM {table} GROUP BY org_id ORDER BY n DESC"
            )
            shown = ", ".join(f"{r['org_id']}={r['n']}" for r in rows) or "(none)"
            print(f"  {table:22} by org: {shown}")

        print(f"\n=== Dev-org graph census ({DEV_ORG}) ===")
        for table in GRAPH_TABLES:
            n = await conn.fetchval(
                f"SELECT count(*) FROM {table} WHERE org_id = $1", DEV_ORG
            )
            print(f"  {table:22} {n}")

        # A couple of headline DQ smell-tests, cheap to grab now.
        print(f"\n=== Quick smell tests (dev org) ===")
        failed = await conn.fetchval(
            "SELECT count(*) FROM email_message WHERE org_id=$1 AND parse_status='failed'", DEV_ORG
        )
        total_msg = await conn.fetchval(
            "SELECT count(*) FROM email_message WHERE org_id=$1", DEV_ORG
        )
        null_name = await conn.fetchval(
            "SELECT count(*) FROM person WHERE org_id=$1 AND (display_name IS NULL OR btrim(display_name)='')",
            DEV_ORG,
        )
        total_person = await conn.fetchval(
            "SELECT count(*) FROM person WHERE org_id=$1", DEV_ORG
        )
        recip_null = await conn.fetchval(
            "SELECT count(*) FROM email_recipient WHERE org_id=$1 AND person_id IS NULL", DEV_ORG
        )
        total_recip = await conn.fetchval(
            "SELECT count(*) FROM email_recipient WHERE org_id=$1", DEV_ORG
        )
        print(f"  parse_status=failed     {failed}/{total_msg}")
        print(f"  person blank/null name  {null_name}/{total_person}")
        print(f"  recipient person_id NULL {recip_null}/{total_recip}")
    finally:
        await conn.close()


asyncio.run(main())
