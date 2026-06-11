"""TC-IM-E01 — Live RLS isolation on the entity graph (person / person_email).

Break hypothesis: RLS is ENABLE+FORCE'd on every tenant table per migration 0009, but the
standing-invariant test only LIVE-proves it on `users`; every entity/email functional test runs on
the BYPASSRLS global engine. So DB-level isolation on the densest-PII tables (person, person_email,
email_*) is catalog-proven, not row-proven. This drives the real `oneai_app` (NOBYPASSRLS) role and
checks whether a cross-tenant SELECT/INSERT is actually blocked at the database.

Run (testing/ is not mounted; pipe over stdin into the backend container):
    docker compose exec -T backend python - < testing/10_imap-connector/harness/rls_entity_isolation.py

Non-destructive: seeds two RUN-STAMPED throwaway orgs, asserts, then deletes only its own rows.
Never touches the demo org. Read-only against any pre-existing data.
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
TAG = f"rls-e01-{STAMP}"


def dsn(user: str, password: str) -> str:
    return f"postgresql://{user}:{password}@{S.postgres_host}:{S.postgres_port}/{S.postgres_db}"


OWNER = dsn(S.postgres_user, S.postgres_password)            # oneai (super+bypassrls) — DDL/seed
APP = dsn(S.app_db_user, S.oneai_app_password)               # oneai_app (NOBYPASSRLS) — RLS bites
GLOBAL = dsn(S.global_db_user, S.oneai_global_password)      # oneai_global (BYPASSRLS) — the teeth

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} :: {detail}")


async def seed(owner: asyncpg.Connection) -> tuple[uuid.UUID, uuid.UUID]:
    pa = await owner.fetchval(
        "INSERT INTO person (org_id, display_name, is_internal) VALUES ($1,$2,false) RETURNING id",
        ORG_A, f"A {TAG}",
    )
    pb = await owner.fetchval(
        "INSERT INTO person (org_id, display_name, is_internal) VALUES ($1,$2,false) RETURNING id",
        ORG_B, f"B {TAG}",
    )
    # SAME email in both orgs — only legal because uq_person_email_identity is (org_id, email).
    for org, pid in ((ORG_A, pa), (ORG_B, pb)):
        await owner.execute(
            "INSERT INTO person_email (org_id, person_id, email) VALUES ($1,$2,$3)",
            org, pid, f"shared-{STAMP}@example.test",
        )
    return pa, pb


async def visible_orgs(conn: asyncpg.Connection, guc: uuid.UUID | None) -> set[str]:
    """Return the set of org_ids visible for our TAG'd persons, under an optional org GUC."""
    async with conn.transaction():
        if guc is not None:
            await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(guc))
        rows = await conn.fetch(
            "SELECT org_id FROM person WHERE display_name LIKE $1", f"% {TAG}"
        )
    return {str(r["org_id"]) for r in rows}


async def main() -> None:
    owner = await asyncpg.connect(OWNER)
    app = await asyncpg.connect(APP)
    glob = await asyncpg.connect(GLOBAL)
    try:
        await seed(owner)
        print(f"seeded ORG_A={ORG_A} ORG_B={ORG_B} (tag {TAG})")

        # 1. oneai_app scoped to ORG_A sees ONLY org A's person.
        seen_a = await visible_orgs(app, ORG_A)
        check("app_scoped_A_sees_only_A", seen_a == {str(ORG_A)}, f"visible={seen_a}")

        # 2. oneai_app scoped to ORG_B sees ONLY org B's person.
        seen_b = await visible_orgs(app, ORG_B)
        check("app_scoped_B_sees_only_B", seen_b == {str(ORG_B)}, f"visible={seen_b}")

        # 3. Fail-closed: a FRESH oneai_app connection that NEVER set the GUC sees ZERO rows
        #    (truly-unset placeholder GUC -> current_setting returns NULL -> org_id = NULL -> no match).
        #    Note: a connection that set-then-reverted a local GUC leaves it as '' (empty), which makes
        #    the policy's ''::uuid ERROR instead of returning [] — still fail-closed (no leak), but the
        #    real app sets the GUC on every transaction (after_begin), so it never relies on the unset path.
        app_fresh = await asyncpg.connect(APP)
        try:
            seen_none = await visible_orgs(app_fresh, None)
        finally:
            await app_fresh.close()
        check("app_fresh_unset_guc_sees_nothing", seen_none == set(), f"visible={seen_none}")

        # 4. THE TEETH: oneai_global (BYPASSRLS) sees BOTH — proves the test isn't vacuously green
        #    (rows exist; it's RLS, not absence, that hides them from the app role).
        seen_global = await visible_orgs(glob, None)
        check(
            "global_bypassrls_sees_both",
            seen_global == {str(ORG_A), str(ORG_B)},
            f"visible={seen_global}",
        )

        # 5. person_email per-org isolation: app scoped to A sees ONE row for the shared address.
        async with app.transaction():
            await app.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
            pe = await app.fetch(
                "SELECT org_id FROM person_email WHERE email=$1", f"shared-{STAMP}@example.test"
            )
        check(
            "person_email_per_org_isolation",
            len(pe) == 1 and str(pe[0]["org_id"]) == str(ORG_A),
            f"rows={len(pe)} orgs={[str(r['org_id']) for r in pe]}",
        )

        # 6. WITH CHECK: oneai_app scoped to A cannot INSERT a row tagged org B.
        insert_rejected = False
        detail = "INSERT SUCCEEDED (LEAK)"
        try:
            async with app.transaction():
                await app.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
                await app.execute(
                    "INSERT INTO person (org_id, display_name, is_internal) VALUES ($1,$2,false)",
                    ORG_B, f"X {TAG}",
                )
        except asyncpg.PostgresError as exc:
            insert_rejected = "row-level security" in str(exc).lower()
            detail = f"{type(exc).__name__}: {str(exc)[:90]}"
        check("app_cross_org_insert_rejected", insert_rejected, detail)

        # 7. Cross-org UPDATE: app scoped to A cannot move its own row to org B (WITH CHECK).
        update_rejected = False
        detail = "UPDATE SUCCEEDED (LEAK)"
        try:
            async with app.transaction():
                await app.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
                await app.execute(
                    "UPDATE person SET org_id=$1 WHERE display_name=$2", ORG_B, f"A {TAG}"
                )
        except asyncpg.PostgresError as exc:
            update_rejected = "row-level security" in str(exc).lower()
            detail = f"{type(exc).__name__}: {str(exc)[:90]}"
        check("app_cross_org_update_rejected", update_rejected, detail)

    finally:
        # Cleanup: delete ONLY our run-stamped rows (person_email cascades from person).
        deleted = await owner.fetch(
            "DELETE FROM person WHERE display_name LIKE $1 RETURNING id", f"% {TAG}"
        )
        print(f"cleanup: deleted {len(deleted)} person rows (+cascade)")
        await owner.close()
        await app.close()
        await glob.close()

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\nRESULT: {passed}/{len(results)} checks passed")
    print("VERDICT:", "RLS HOLDS on the entity graph" if passed == len(results)
          else "RLS GAP — cross-tenant exposure")


asyncio.run(main())
