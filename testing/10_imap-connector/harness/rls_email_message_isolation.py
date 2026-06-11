"""TC-IM-E02 — Live RLS isolation on the email Layer-1 tables (email_message / email_recipient /
email_attachment) as the real NOBYPASSRLS `oneai_app` role.

Break hypothesis: E01 proved RLS bites on person/person_email. The email Layer-1 tables carry the
DENSEST PII (subject, body, recipient addresses) and got their org_isolation policy in migration 0008
+ FORCE in 0009 — but, like the entity graph, every functional ingest test runs on the BYPASSRLS
global engine, so DB-level isolation on email_message/recipient/attachment is catalog-proven, not
row-proven. This drives the real `oneai_app` role and checks whether a cross-tenant SELECT is blocked,
whether `oneai_global` (BYPASSRLS) sees both (the teeth), and whether a cross-org INSERT is rejected
by the policy's WITH CHECK.

Run (testing/ is not mounted; pipe over stdin into the backend container):
    docker compose exec -T backend python - < testing/10_imap-connector/harness/rls_email_message_isolation.py

Non-destructive: seeds two RUN-STAMPED throwaway orgs (a connector_connection + one email_message +
recipient + attachment each) via the OWNER engine, asserts, then deletes only its own rows in a
finally block. Never touches the demo org. Read-only against any pre-existing data.
"""
from __future__ import annotations

import asyncio
import uuid

import asyncpg

from app.core.config import get_settings

S = get_settings()
STAMP = uuid.uuid4().hex[:12]
ORG_A = uuid.uuid4()
ORG_B = uuid.uuid4()
TAG = f"rls-e02-{STAMP}"
SUBJECT = f"subject {TAG}"


def dsn(user: str, password: str) -> str:
    return f"postgresql://{user}:{password}@{S.postgres_host}:{S.postgres_port}/{S.postgres_db}"


OWNER = dsn(S.postgres_user, S.postgres_password)            # oneai (super+bypassrls) — DDL/seed
APP = dsn(S.app_db_user, S.oneai_app_password)               # oneai_app (NOBYPASSRLS) — RLS bites
GLOBAL = dsn(S.global_db_user, S.oneai_global_password)      # oneai_global (BYPASSRLS) — the teeth

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} :: {detail}")


async def seed_connection(owner: asyncpg.Connection, org: uuid.UUID) -> uuid.UUID:
    """Insert a minimal connector_connection (opaque ciphertext) so an email can FK it."""
    return await owner.fetchval(
        """
        INSERT INTO connector_connection
            (org_id, connector_type, display_name, auth_method, username, config,
             secret_ciphertext, secret_key_version, status)
        VALUES ($1, 'imap', 'Mailbox', 'app_password', $2, '{}'::jsonb, $3, 1, 'configured')
        RETURNING id
        """,
        org, f"owner-{STAMP}@example.test", b"\x00" * 32,
    )


async def seed_email(
    owner: asyncpg.Connection, org: uuid.UUID, connection_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Insert one email_message (+ recipient + attachment) under (org, connection)."""
    msg_id = await owner.fetchval(
        """
        INSERT INTO email_message
            (org_id, connection_id, dedup_key, subject, parse_status, headers)
        VALUES ($1, $2, $3, $4, 'parsed', '{}'::jsonb)
        RETURNING id
        """,
        org, connection_id, f"dedup-{STAMP}", SUBJECT,
    )
    rcpt_id = await owner.fetchval(
        """
        INSERT INTO email_recipient (org_id, email_id, kind, address)
        VALUES ($1, $2, 'to', $3)
        RETURNING id
        """,
        org, msg_id, f"to-{STAMP}@example.test",
    )
    att_id = await owner.fetchval(
        """
        INSERT INTO email_attachment (org_id, email_id, filename)
        VALUES ($1, $2, $3)
        RETURNING id
        """,
        org, msg_id, f"file-{STAMP}.txt",
    )
    return msg_id, rcpt_id, att_id


async def visible_msg_orgs(conn: asyncpg.Connection, guc: uuid.UUID | None) -> set[str]:
    """Return the set of org_ids visible for our TAG'd email_message rows under an optional GUC."""
    async with conn.transaction():
        if guc is not None:
            await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(guc))
        rows = await conn.fetch("SELECT org_id FROM email_message WHERE subject = $1", SUBJECT)
    return {str(r["org_id"]) for r in rows}


async def count_children(conn: asyncpg.Connection, table: str, guc: uuid.UUID, like: str) -> int:
    """Count TAG'd child rows (recipient/attachment) visible under the given org GUC."""
    column = "address" if table == "email_recipient" else "filename"
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(guc))
        rows = await conn.fetch(f"SELECT id FROM {table} WHERE {column} LIKE $1", like)
    return len(rows)


async def main() -> None:
    owner = await asyncpg.connect(OWNER)
    app = await asyncpg.connect(APP)
    glob = await asyncpg.connect(GLOBAL)
    try:
        conn_a = await seed_connection(owner, ORG_A)
        conn_b = await seed_connection(owner, ORG_B)
        await seed_email(owner, ORG_A, conn_a)
        await seed_email(owner, ORG_B, conn_b)
        print(f"seeded ORG_A={ORG_A} ORG_B={ORG_B} (tag {TAG})")

        # 1. oneai_app scoped to ORG_A sees ONLY org A's email_message.
        seen_a = await visible_msg_orgs(app, ORG_A)
        check("app_scoped_A_sees_only_A", seen_a == {str(ORG_A)}, f"visible={seen_a}")

        # 2. oneai_app scoped to ORG_B sees ONLY org B's email_message.
        seen_b = await visible_msg_orgs(app, ORG_B)
        check("app_scoped_B_sees_only_B", seen_b == {str(ORG_B)}, f"visible={seen_b}")

        # 3. THE TEETH: oneai_global (BYPASSRLS) sees BOTH — proves rows EXIST; it's RLS, not absence,
        #    that hides them from the app role.
        seen_global = await visible_msg_orgs(glob, None)
        check(
            "global_bypassrls_sees_both",
            seen_global == {str(ORG_A), str(ORG_B)},
            f"visible={seen_global}",
        )

        # 4. email_recipient per-org isolation: BOTH orgs' recipients share the run-stamp `to-<STAMP>@`,
        #    so the global role sees 2; the app role scoped to A must see ONLY 1 (org A's).
        rcpt_global = await count_children(glob, "email_recipient", ORG_A, f"to-{STAMP}@%")
        rcpt_a = await count_children(app, "email_recipient", ORG_A, f"to-{STAMP}@%")
        check(
            "recipient_per_org_isolation",
            rcpt_global == 2 and rcpt_a == 1,
            f"global sees {rcpt_global} (both orgs), A-scoped app sees {rcpt_a} (only A)",
        )

        # 5. email_attachment per-org isolation: BOTH orgs' attachments share `file-<STAMP>.txt`,
        #    so global sees 2; app scoped to A must see ONLY 1.
        att_global = await count_children(glob, "email_attachment", ORG_A, f"file-{STAMP}.txt")
        att_a = await count_children(app, "email_attachment", ORG_A, f"file-{STAMP}.txt")
        check(
            "attachment_per_org_isolation",
            att_global == 2 and att_a == 1,
            f"global sees {att_global} (both orgs), A-scoped app sees {att_a} (only A)",
        )

        # 6. WITH CHECK: oneai_app scoped to A cannot INSERT an email_message tagged org B.
        insert_rejected = False
        detail = "INSERT SUCCEEDED (LEAK)"
        try:
            async with app.transaction():
                await app.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
                await app.execute(
                    """
                    INSERT INTO email_message
                        (org_id, connection_id, dedup_key, subject, parse_status, headers)
                    VALUES ($1, $2, $3, $4, 'parsed', '{}'::jsonb)
                    """,
                    ORG_B, conn_b, f"evil-{STAMP}", f"evil {TAG}",
                )
        except asyncpg.PostgresError as exc:
            insert_rejected = "row-level security" in str(exc).lower()
            detail = f"{type(exc).__name__}: {str(exc)[:90]}"
        check("app_cross_org_message_insert_rejected", insert_rejected, detail)

        # 7. Cross-org UPDATE: app scoped to A cannot move its own email_message to org B (WITH CHECK).
        update_rejected = False
        detail = "UPDATE SUCCEEDED (LEAK)"
        try:
            async with app.transaction():
                await app.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
                await app.execute(
                    "UPDATE email_message SET org_id=$1 WHERE subject=$2", ORG_B, SUBJECT
                )
        except asyncpg.PostgresError as exc:
            update_rejected = "row-level security" in str(exc).lower()
            detail = f"{type(exc).__name__}: {str(exc)[:90]}"
        check("app_cross_org_message_update_rejected", update_rejected, detail)

    finally:
        # Cleanup: delete ONLY our run-stamped connections (email + children CASCADE from it).
        deleted = await owner.fetch(
            "DELETE FROM connector_connection WHERE username LIKE $1 RETURNING id",
            f"owner-{STAMP}@%",
        )
        print(f"cleanup: deleted {len(deleted)} connector_connection rows (+cascade email/children)")
        await owner.close()
        await app.close()
        await glob.close()

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\nRESULT: {passed}/{len(results)} checks passed")
    print("VERDICT:", "RLS HOLDS on the email Layer-1 tables" if passed == len(results)
          else "RLS GAP — cross-tenant email exposure")


asyncio.run(main())
